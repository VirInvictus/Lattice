import ast
import operator
import os
import re
import sys
from typing import Any, Callable

from lattice.utils import (
    count_audio_files,
    _make_pbar,
    is_audio,
    parse_layout,
    iter_audio_dirs,
    as_roots,
)
from lattice.tags import get_all_tags

# =====================================
# Mode: Playlist generation (.m3u)
# =====================================


# Smart-playlist rules are evaluated by walking a whitelisted AST, never by
# eval(). eval with `{"__builtins__": {}}` is NOT a sandbox — a rule like
# `genre.__class__.__mro__[-1].__subclasses__()` escapes it to arbitrary code.
# Here only comparisons, boolean/arithmetic operators, the exposed field names,
# and literal constants are allowed; attribute access, calls, and subscripts
# raise, so a rule can read the fields and nothing else.
_CMP_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class RuleError(Exception):
    """A rule referenced an unknown field or used an unsupported construct."""


def _eval_node(node: ast.AST, names: dict):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, names)
    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, names) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, names))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](
            _eval_node(node.left, names), _eval_node(node.right, names)
        )
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, names)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if type(op) not in _CMP_OPS:
                raise RuleError(f"operator {type(op).__name__} not allowed")
            right = _eval_node(comparator, names)
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise RuleError(f"unknown field '{node.id}'")
    if isinstance(node, ast.Constant):
        return node.value
    raise RuleError(f"unsupported expression: {type(node).__name__}")


# SQL-style AND/OR are folded to Python's and/or, but only outside quoted
# strings — a plain str.replace would corrupt a literal like 'Drum AND Bass'.
# re.split with a capturing group keeps the quoted segments at odd indices.
_QUOTED_SEGMENT = re.compile(r"('[^']*'|\"[^\"]*\")")
_SQL_AND = re.compile(r"\bAND\b")
_SQL_OR = re.compile(r"\bOR\b")


def _pythonize_rule(rule: str) -> str:
    parts = _QUOTED_SEGMENT.split(rule)
    for i in range(0, len(parts), 2):
        parts[i] = _SQL_OR.sub("or", _SQL_AND.sub("and", parts[i]))
    return "".join(parts)


def validate_rule(rule: str) -> str | None:
    """One-shot check of a smart rule against dummy metadata, so a rule that
    can never evaluate (syntax error, unknown field, type mismatch) is one
    error before the walk instead of one stderr line per track. Returns the
    error message, or None when the rule is usable."""
    if not rule or not rule.strip():
        return None
    names = {
        "rating": 0.0,
        "genre": "",
        "artist": "",
        "album": "",
        "title": "",
        "duration": 0.0,
        "bitrate": 0,
    }
    try:
        _eval_node(ast.parse(_pythonize_rule(rule), mode="eval"), names)
    except Exception as e:
        return str(e)
    return None


def _evaluate_rule(rule: str, t, parsed_layout: dict) -> bool:
    """Evaluate a dynamic smart playlist rule against a track's metadata, using
    a restricted AST walker (see _eval_node) rather than eval()."""
    if not rule or not rule.strip():
        return True

    names = {
        "rating": t.rating or 0.0,
        "genre": t.genre or "",
        "artist": t.artist or parsed_layout.get("artist", ""),
        "album": t.album or parsed_layout.get("album", ""),
        "title": t.title or parsed_layout.get("title", ""),
        "duration": t.duration_s or 0.0,
        "bitrate": t.bitrate_kbps or 0,
    }

    try:
        # Accept SQL-style AND/OR as a convenience for Python's and/or.
        return bool(_eval_node(ast.parse(_pythonize_rule(rule), mode="eval"), names))
    except Exception as e:
        print(f"Error evaluating rule '{rule}': {e}", file=sys.stderr)
        return False


def generate_playlist(
    root_dir: str | list[str],
    output_file: str,
    rule: str,
    layout: str = "{artist}/{album}",
    quiet: bool = False,
) -> int:
    """Generate an .m3u playlist based on a smart rule filter."""
    rule_error = validate_rule(rule)
    if rule_error is not None:
        print(f"Invalid rule '{rule}': {rule_error}", file=sys.stderr)
        return 1

    roots = as_roots(root_dir)
    total_files = count_audio_files(roots)

    if total_files == 0:
        if not quiet:
            print(f"No audio files found under: {', '.join(roots)}")
        return 0

    if not quiet:
        print(f"Scanning {total_files} files for playlist generation...")

    pbar = _make_pbar(total_files, "Building playlist", quiet)

    playlist_entries: list[str] = []

    for src_root, dirpath, _dirs, files in iter_audio_dirs(roots):
        # Sort files to keep album tracks in order
        for f in sorted(files):
            if is_audio(f):
                filepath = os.path.join(dirpath, f)
                rel_path = os.path.relpath(filepath, src_root)
                parsed = parse_layout(rel_path, layout)
                t = get_all_tags(filepath)

                if _evaluate_rule(rule, t, parsed):
                    # For .m3u, we can write #EXTINF if we have duration and title
                    duration = int(t.duration_s) if t.duration_s else -1
                    artist = t.artist or parsed.get("artist", "Unknown")
                    title = t.title or f
                    display = f"{artist} - {title}" if artist != "Unknown" else title

                    playlist_entries.append(f"#EXTINF:{duration},{display}")
                    # Use absolute paths for the playlist
                    playlist_entries.append(filepath)

                pbar.update(1)

    pbar.close()

    if not playlist_entries:
        if not quiet:
            print(f"No tracks matched the rule: {rule}")
        return 0

    out_path = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for entry in playlist_entries:
                f.write(f"{entry}\n")
    except OSError as e:
        print(f"Failed to write playlist: {e}", file=sys.stderr)
        return 1

    if not quiet:
        track_count = len(playlist_entries) // 2
        print(f"\nWrote playlist with {track_count} tracks to: {out_path}")

    return 0
