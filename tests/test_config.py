import os
import unittest
from unittest import mock

from lattice import config


class GetLayoutTests(unittest.TestCase):
    def test_default_when_unset(self):
        with mock.patch.object(config, "load_config", return_value={}):
            self.assertEqual(config.get_layout(), config.DEFAULT_LAYOUT)

    def test_config_value_wins(self):
        with mock.patch.object(
            config, "load_config", return_value={"layout": "{genre}/{artist}/{album}"}
        ):
            self.assertEqual(config.get_layout(), "{genre}/{artist}/{album}")

    def test_empty_value_falls_back_to_default(self):
        with mock.patch.object(config, "load_config", return_value={"layout": ""}):
            self.assertEqual(config.get_layout(), config.DEFAULT_LAYOUT)


class LibraryRootExpansionTests(unittest.TestCase):
    """The config invites hand-editing, so a hand-written "~/Music" must expand
    the same way the plural library_roots branch always has."""

    def test_single_root_expands_user(self):
        with mock.patch.object(
            config, "load_config", return_value={"library_root": "~/Music"}
        ):
            root = config.get_library_root()
            self.assertIsNotNone(root)
            self.assertTrue(os.path.isabs(root))
            self.assertNotIn("~", root)
            self.assertEqual(config.get_library_roots(), [root])

    def test_unset_root_is_none(self):
        with mock.patch.object(config, "load_config", return_value={}):
            self.assertIsNone(config.get_library_root())
            self.assertEqual(config.get_library_roots(), [])


if __name__ == "__main__":
    unittest.main()
