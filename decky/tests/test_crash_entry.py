"""Decky crash reporting starts and stops with the plugin, without real I/O."""

import asyncio
import unittest
from unittest import mock

from test_backend import m


class CrashEntryTest(unittest.TestCase):
    def test_lifecycle_installs_shared_preference_loop_and_task_hooks(self):
        async def exercise():
            plugin = m.Plugin()
            reporter = mock.Mock()
            with mock.patch.object(m.crash_report, "install", return_value=reporter) as install, \
                    mock.patch.object(plugin, "_enforce_max_fans", new_callable=mock.AsyncMock), \
                    mock.patch.object(plugin, "_watch_desktop_app", new_callable=mock.AsyncMock), \
                    mock.patch.object(m, "_load_max_fans", return_value={}), \
                    mock.patch.object(m.os.path, "exists", return_value=False):
                await plugin._main()
                install.assert_called_once_with(config_path=m.INSTALL_JSON, logger=m.decky.logger,
                                                loop=asyncio.get_running_loop(),
                                                fatal_name="fatal-decky.log")
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertEqual(reporter.task_done.call_count, 2)
                await plugin._unload()
                reporter.close.assert_called_once_with()

        asyncio.run(exercise())
