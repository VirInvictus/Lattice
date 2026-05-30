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


if __name__ == "__main__":
    unittest.main()
