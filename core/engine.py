import asyncio
import MetaTrader5 as mt5
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

class TradingEngine:
    def __init__(self, bot_manager):
        self.bot_manager = bot_manager
        self.running = True
        self.last_tick_time = time.time()
        
        # MT5 Configuration
        self.login = int(os.getenv("MT5_LOGIN", 0))
        self.password = os.getenv("MT5_PASSWORD", "")
        self.server = os.getenv("MT5_SERVER", "")
        self.path = os.getenv("MT5_PATH", "")

    async def start(self):
        print(" Engine: Initializing Direct MT5 Connection (Monolith)...")
        
        if not mt5.initialize(path=self.path):
            print(f" MT5 Init Failed: {mt5.last_error()}")
            return
            
        if not mt5.login(self.login, password=self.password, server=self.server):
            print(f" MT5 Login Failed: {mt5.last_error()}")
            return
            
        print(" MT5 Connected. Starting High-Speed Loop.")
        await self.run_tick_loop()

    async def run_tick_loop(self):
        current_symbol = "FX Vol 20"
        
        while self.running:
            try:
                bots = list(self.bot_manager.bots.values())
                if bots:
                    current_symbol = bots[0].config.get('symbol', current_symbol)
                    mt5.symbol_select(current_symbol, True)
                    
                    tick = mt5.symbol_info_tick(current_symbol)
                    
                    if tick:
                        # UPDATE HEARTBEAT ON SUCCESSFUL POLL
                        self.last_tick_time = time.time()
                        
                        positions = mt5.positions_get(symbol=current_symbol)
                        pos_count = len(positions) if positions else 0
                        
                        tick_data = {
                            'ask': tick.ask, 
                            'bid': tick.bid,
                            'positions_count': pos_count
                        }
                        
                        await asyncio.gather(*[bot.on_external_tick(tick_data) for bot in bots])
                        
            except Exception as e:
                print(f"Engine Error: {e}")
                if "connection lost" in str(e).lower():
                    print(" Critical MT5 Error. Committing Suicide to force Restart.")
                    os._exit(1)
                
            await asyncio.sleep(0)

    async def watchdog(self):
        """Kills the app if the engine freezes for > 30 seconds"""
        while True:
            await asyncio.sleep(5)
            time_since_tick = time.time() - self.last_tick_time
            
            if time_since_tick > 30:
                print(f" WATCHDOG: Engine frozen for {time_since_tick}s. Killing process.")
                os._exit(1)

    async def stop(self):
        self.running = False
        mt5.shutdown()
        print(" MT5 Disconnected.")