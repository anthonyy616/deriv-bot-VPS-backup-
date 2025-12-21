import json
import os
from typing import Dict, Any

class ConfigManager:
    def __init__(self, user_id: str = "default", config_file: str = "config.json"):
        self.user_id = user_id
        
        if user_id and user_id != "default":
            self.config_file = f"config_{user_id}.json"
        else:
            self.config_file = config_file
            
        self.config: Dict[str, Any] = {}
        # Load once on startup
        self.load_config()

    def load_config(self):
        default_keys = self._get_defaults().keys()
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    loaded_data = json.load(f)
                    
                # Sanitize: Keep valid keys, fill missing defaults
                self.config = {k: v for k, v in loaded_data.items() if k in default_keys}
                
                defaults = self._get_defaults()
                for k, v in defaults.items():
                    if k not in self.config:
                        self.config[k] = v
                        
            except Exception as e:
                print(f" Error loading config {self.config_file}: {e}")
                # Don't overwrite memory with defaults immediately if just a read error,
                # but for safety on startup we must.
                if not self.config: 
                    self.config = self._get_defaults()
        else:
            print(f" Creating new config file: {self.config_file}")
            self.config = self._get_defaults()
            self.save_config()

    def save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f" Error saving config: {e}")

    def update_config(self, new_config: Dict[str, Any]):
        # Update Memory FIRST (Instant)
        self.config.update(new_config)
        # Then save to disk (Persist)
        self.save_config()
        return self.config

    def get_config(self):
        # Serve from Memory (Fast & Thread-Safe)
        return self.config

    def _get_defaults(self):
        return {
            "symbol": "FX Vol 20",
            "spread": 8.0,
            "max_positions": 5,
            "step_lots": [0.01, 0.01, 0.01, 0.01, 0.01],
            "buy_stop_tp": 16.0,
            "buy_stop_sl": 24.0,
            "sell_stop_tp": 16.0,
            "sell_stop_sl": 24.0,
            "max_runtime_minutes": 0,
            "max_drawdown_usd": 50.0
        }