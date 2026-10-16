from __future__ import annotations

import importlib
import unittest

from openttd_mcp import runtime


class OpenTTDRuntimeNoiseTests(unittest.TestCase):
    def test_runtime_scripts_share_runtime_module(self):
        # Guards the de-duplication: every runner imports the shared paths and
        # symlink_or_copy from openttd_mcp.runtime, and still imports cleanly.
        for name in (
            "openttd_mcp.verify._scaffold",
            "openttd_mcp.launcher",
        ):
            with self.subTest(name=name):
                module = importlib.import_module(name)
                self.assertIs(module.symlink_or_copy, runtime.symlink_or_copy)
                self.assertEqual(module.OTTD_BIN, runtime.OTTD_BIN)


if __name__ == "__main__":
    unittest.main()
