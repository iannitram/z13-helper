import json
import os
import logging
import shutil
import threading
import time

logger = logging.getLogger(__name__)

class ConfigManager:
    """Manages application configuration using a JSON file."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = {}
        # set() is called from the Qt thread and from the ApplyWorker thread.
        self._lock = threading.Lock()
        self.load_config()

    def load_config(self):
        """Loads configuration from the JSON file."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    # Valid JSON of the wrong shape: callers treat config as a
                    # dict. Keep the file for recovery, start clean.
                    backup = f"{self.config_path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
                    logger.error(f"Config at {self.config_path} is a {type(data).__name__}, "
                                 f"not an object; kept as {backup}, starting with defaults")
                    try:
                        shutil.copy2(self.config_path, backup)
                    except OSError as e:
                        logger.error(f"Could not keep a backup of {self.config_path}: {e}")
                    self.config = {}
                else:
                    self.config = data
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load config from {self.config_path}: {e}")
                self.config = {}
        else:
            logger.info(f"Config file not found at {self.config_path}. Starting with empty config.")
            self.config = {}

    def save_config(self):
        """Saves the current configuration to the JSON file (tmp + rename, so a
        crash or a concurrent reader never sees a half-written file)."""
        with self._lock:
            tmp = f"{self.config_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                dir_name = os.path.dirname(self.config_path)
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)
                with open(tmp, 'w') as f:
                    json.dump(self.config, f, indent=4)
                os.replace(tmp, self.config_path)
            except (OSError, TypeError, ValueError) as e:
                logger.error(f"Failed to save config to {self.config_path}: {e}")
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def get(self, key: str, default=None):
        """Gets a configuration value."""
        return self.config.get(key, default)

    def set(self, key: str, value):
        """Sets a configuration value and saves it."""
        with self._lock:
            self.config[key] = value
        self.save_config()

if __name__ == "__main__":
    import os
    # Test config manager in current directory
    test_config = "test_config.json"
    cm = ConfigManager(test_config)
    
    print("Testing ConfigManager...")
    cm.set("battery_limit", 80)
    cm.set("profile", "balanced")
    
    print(f"Battery Limit: {cm.get('battery_limit')}")
    print(f"Profile: {cm.get('profile')}")
    print(f"Non-existent key: {cm.get('non_existent', 'DefaultValue')}")

    # Cleanup
    if os.path.exists(test_config):
        os.remove(test_config)
