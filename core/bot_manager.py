import uuid
from typing import Dict
from core.config_manager import ConfigManager
from core.strategy_engine import GridStrategy

class BotManager:
    def __init__(self):
        # Maps user_id -> GridStrategy
        self.bots: Dict[str, GridStrategy] = {}
        self.state_file = "bot_state.json"
        # On server startup, check if we need to auto-resume
        # We can't use asyncio here easily, so we rely on the server.py startup event to trigger recovery

    async def restore_sessions(self):
        """Called by server.py on startup to resurrect dead bots"""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, 'r') as f:
                active_users = json.load(f)
            
            print(f"🧟 CRASH RECOVERY: Found {len(active_users)} active sessions. Resurrecting...")
            
            for user_id in active_users:
                # 1. Re-initialize the bot
                bot = await self.get_or_create_bot(user_id)
                # 2. Force start it immediately
                print(f"⚡ Auto-starting bot for {user_id}...")
                await bot.start()
                
        except Exception as e:
            print(f"❌ Failed to restore sessions: {e}")

    async def get_or_create_bot(self, user_id: str) -> GridStrategy:
        if user_id in self.bots:
            return self.bots[user_id]
        
        # Load Config & Strategy
        print(f"🔄 Loading bot resources for User: {user_id}")
        config_manager = ConfigManager(user_id=user_id)
        strategy = GridStrategy(config_manager)
        
        # Start the background price ticker (it's passive, so safe to always run)
        await strategy.start_ticker()
        
        self.bots[user_id] = strategy
        return strategy

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
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    current_users = json.load(f)
            except: pass
        
        if add and user_id not in current_users:
            current_users.append(user_id)
        elif not add and user_id in current_users:
            current_users.remove(user_id)
            
        with open(self.state_file, 'w') as f:
            json.dump(current_users, f)