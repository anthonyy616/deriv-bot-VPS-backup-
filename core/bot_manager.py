import uuid
import os
import json
from typing import Dict
from core.config_manager import ConfigManager
from core.strategy_engine import GridStrategy

class BotManager:
    def __init__(self):
        # Maps user_id -> GridStrategy
        self.bots: Dict[str, GridStrategy] = {}
        
        # FIX: Use a unique filename for the session list.
        # This prevents conflict with the strategy's "bot_state.json"
        self.state_file = "active_users.json" 

    async def restore_sessions(self):
        """Called by server.py on startup to resurrect dead bots"""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, 'r') as f:
                active_users = json.load(f)
            
            # Ensure we actually loaded a list (Safety Check)
            if not isinstance(active_users, list):
                print(" Warning: active_users.json was corrupted. Resetting.")
                active_users = []

            if active_users:
                print(f" CRASH RECOVERY: Found {len(active_users)} active sessions. Resurrecting...")
            
            for user_id in active_users:
                # 1. Re-initialize the bot
                bot = await self.get_or_create_bot(user_id)
                # 2. Force start it immediately
                print(f" Auto-starting bot for {user_id}...")
                await bot.start()
                
        except Exception as e:
            print(f" Failed to restore sessions: {e}")
            # If the file is broken, delete it so we can start fresh
            if os.path.exists(self.state_file):
                os.remove(self.state_file)

    async def get_or_create_bot(self, user_id: str) -> GridStrategy:
        if user_id in self.bots:
            return self.bots[user_id]
        
        # Load Config & Strategy
        print(f" Loading bot resources for User: {user_id}")
        config_manager = ConfigManager(user_id=user_id)
        strategy = GridStrategy(config_manager)
        
        # Start the background price ticker (it's passive, so safe to always run)
        await strategy.start_ticker()
        
        self.bots[user_id] = strategy
        return strategy

    def get_bot(self, user_id: str) -> GridStrategy:
        return self.bots.get(user_id)

    async def start_bot(self, user_id: str):
        """Wrapper to start bot and save state to disk"""
        bot = await self.get_or_create_bot(user_id)
        await bot.start()
        self._update_state_file(user_id, add=True)

    async def stop_bot(self, user_id: str):
        """Wrapper to stop bot and remove state from disk"""
        bot = self.bots.get(user_id)
        if bot:
            await bot.stop()
            self._update_state_file(user_id, add=False)
            print(f"Bot stopped for user: {user_id}")

    def _update_state_file(self, user_id: str, add: bool):
        """Writes the list of currently running users to disk"""
        current_users = []
        
        # Read existing list
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        current_users = data
            except: pass
        
        # Modify list
        if add and user_id not in current_users:
            current_users.append(user_id)
        elif not add and user_id in current_users:
            current_users.remove(user_id)
            
        # Write back
        try:
            with open(self.state_file, 'w') as f:
                json.dump(current_users, f)
        except Exception as e:
            print(f"Error saving active users: {e}")