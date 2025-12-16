import asyncio
import time
import json
import os
import MetaTrader5 as mt5
import aiohttp

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        self.session = None
        
        # --- IMMUTABLE GRID ANCHORS ---
        self.anchor_center_bid = None 
        self.anchor_center_ask = None
        self.anchor_top_ask = None
        self.anchor_bottom_bid = None
        
        # --- State Memory ---
        self.buy_trigger_name = None   
        self.sell_trigger_name = None
        self.active_upper_level = None
        self.active_lower_level = None
        
        # --- General State ---
        self.current_step = 0
        self.iteration = 1
        self.is_resetting = False 
        self.reset_timestamp = 0
        
        # --- Race Condition Locks ---
        self.is_busy = False 
        self.last_trade_time = 0 # Debounce timer
        
        # --- UI Data ---
        self.current_price = 0.0
        self.open_positions = 0 
        self.start_time = 0
        self.last_pos_count = 0
        
        self.load_state()

    @property
    def config(self):
        self.config_manager.load_config()
        return self.config_manager.get_config()

    async def start_ticker(self):
        print("🔄 Config Change: Forcing Grid Reset...")
        self.is_resetting = True
        self.reset_timestamp = time.time()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.start_time = time.time()
        
        self.symbol = self.config.get('symbol', 'FX Vol 20')
        if not mt5.symbol_select(self.symbol, True):
             print(f"❌ Failed to select {self.symbol}")

        mt5.symbol_select(self.symbol, True)
        
        # --- STARTUP SYNC ---
        real_positions = self.get_real_positions_count()
        
        if real_positions == 0:
            self.cancel_all_orders_direct()
            self.reset_cycle()
        else:
            print(f"⚠️ Resuming existing cycle ({real_positions} positions)...")
            self.last_pos_count = real_positions
            
            # CRITICAL FIX: Sync Step Count with Real Positions on Boot
            if real_positions > self.current_step:
                print(f"🛡️ Startup Sync: Updating Step {self.current_step} -> {real_positions}")
                self.current_step = real_positions
                self.save_state()

        print(f"✅ Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        self.save_state()
        if self.session:
            await self.session.close()

    def get_real_positions_count(self):
        positions = mt5.positions_get(symbol=self.symbol)
        return len(positions) if positions else 0

    def cancel_all_orders_direct(self):
        orders = mt5.orders_get(symbol=self.symbol)
        if orders:
            for order in orders:
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})

    def close_all_direct(self):
        self.cancel_all_orders_direct()
        positions = mt5.positions_get(symbol=self.symbol)
        if positions:
            for pos in positions:
                type_op = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(self.symbol)
                price = tick.bid if type_op == mt5.ORDER_TYPE_SELL else tick.ask
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "position": pos.ticket,
                    "volume": pos.volume,
                    "type": type_op,
                    "price": price,
                    "deviation": 50
                })

    def reset_cycle(self):
        self.anchor_center_bid = None
        self.anchor_top_ask = None
        self.anchor_bottom_bid = None
        self.buy_trigger_name = None
        self.sell_trigger_name = None
        self.active_upper_level = None
        self.active_lower_level = None
        self.current_step = 0
        self.is_resetting = False
        self.is_busy = False 
        self.last_trade_time = 0

        self.save_state()
        print(f"🔄 Cycle Reset: Waiting for new Anchor (Iteration {self.iteration})...")

    async def on_external_tick(self, tick_data):
        if not self.running: return

        # SYMBOL CHECK
        cfg_symbol = self.config.get('symbol')
        if cfg_symbol and cfg_symbol != self.symbol:
            print(f"🔀 Switching Symbol: {self.symbol} -> {cfg_symbol}")
            self.close_all_direct()
            self.symbol = cfg_symbol
            mt5.symbol_select(self.symbol, True)
            self.is_resetting = True
            return

        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 
        self.open_positions = tick_data.get('positions_count', 0)
        
        # 1. NUCLEAR RESET (Safety)
        if self.open_positions < self.last_pos_count and not self.is_resetting and self.current_step > 0:
            print(f"🚨 POSITION DROP DETECTED. NUCLEAR RESET.")
            self.close_all_direct()
            self.is_resetting = True
            self.reset_timestamp = time.time()
            self.last_pos_count = self.open_positions
            return

        # 2. RUNTIME SELF-HEALING (Fixes Race Conditions mid-run)
        # If we see more positions than steps, we missed a save. Fast-forward immediately.
        if self.open_positions > self.current_step and not self.is_resetting:
             print(f"🛡️ Auto-Repair: Catching up Step {self.current_step} -> {self.open_positions}")
             self.current_step = self.open_positions
             self.save_state()

        self.last_pos_count = self.open_positions

        # 3. Reset Handler
        if self.is_resetting:
            if self.open_positions == 0:
                if self.is_time_up():
                    print("🛑 Max Runtime Reached. Stopping.")
                    await self.stop()
                    return
                print("✅ Account Cleaned. Starting New Iteration.")
                self.iteration += 1
                self.reset_cycle()
            else:
                if time.time() - self.reset_timestamp > 2:
                    self.close_all_direct()
                    self.reset_timestamp = time.time()
            return

        # 4. Initialize Grid
        if self.anchor_center_bid is None:
            self.init_immutable_grid(ask, bid)
            return

        # 5. GLOBAL DEBOUNCE
        # Hard ignore of all ticks for 2 seconds after a trade
        if time.time() - self.last_trade_time < 2.0:
            return

        # 6. Check Limits & Locks
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos: return 
        if self.is_time_up(): return
        if self.is_busy: return 

        # 7. LOGIC EXECUTION
        if self.buy_trigger_name == "top":
            if ask >= self.anchor_top_ask:
                print(f"⚡ SNIPER: Hit Top (Ask {ask})")
                self.execute_market_order("buy", ask)
        elif self.buy_trigger_name == "center":
            if ask >= self.anchor_center_ask:
                print(f"⚡ SNIPER: Hit Center (Ask {ask})")
                self.execute_market_order("buy", ask)

        if self.sell_trigger_name == "bottom":
            if bid <= self.anchor_bottom_bid:
                print(f"⚡ SNIPER: Hit Bottom (Bid {bid})")
                self.execute_market_order("sell", bid)
        elif self.sell_trigger_name == "center":
            if bid <= self.anchor_center_bid:
                print(f"⚡ SNIPER: Hit Center (Bid {bid})")
                self.execute_market_order("sell", bid)

    def is_time_up(self):
        max_mins = int(self.config.get('max_runtime_minutes', 0))
        if max_mins == 0: return False
        return (time.time() - self.start_time) / 60 > max_mins

    def init_immutable_grid(self, ask, bid):
        # --- ORIGINAL FORMULA PRESERVED ---
        user_spread = float(self.config.get('spread', 6.0))
        broker_spread = ask - bid
        
        # If user spread is 10 and broker is 1, offset is 9.
        # Top = Ask + 9. Bottom = Bid - 9.
        # Distance between Top and Bottom = (Ask+9) - (Bid-9) = (Ask-Bid) + 18.
        # This keeps the user's visual spread relative to the actual Bid/Ask lines.
        offset = max(user_spread - broker_spread, 0.1)
        
        self.anchor_center_ask = ask
        self.anchor_center_bid = bid
        
        self.anchor_top_ask = ask + offset
        self.anchor_bottom_bid = bid - offset
        
        # Start State: Buy at Top, Sell at Bottom
        self.buy_trigger_name = "top"
        self.sell_trigger_name = "bottom"
        
        print(f"⚓ ANCHOR ({self.symbol}) Set. Offset: {offset:.3f}")
        print(f"   Top (Ask): {self.anchor_top_ask:.5f}")
        print(f"   Bottom (Bid): {self.anchor_bottom_bid:.5f}")
        self.save_state()

    def execute_market_order(self, direction, price):
        if self.is_busy: return
        self.is_busy = True 
        
        try:
            # --- RACE CONDITION KILLER ---
            # Before we even think about trading, check if this step exists in MT5.
            # We are about to execute Step: self.current_step (e.g., Step 0 -> Trade S0)
            # Actually, current_step starts at 0. Step 1 trade will be labeled S0? 
            # Let's align: Trade 1 = "S0". Trade 2 = "S1".
            comment_tag = f"S{self.current_step}"
            
            existing_pos = mt5.positions_get(symbol=self.symbol)
            if existing_pos:
                for p in existing_pos:
                    # Check Magic AND Comment to be 100% sure it's ours and this step
                    if p.magic == self.iteration and comment_tag in p.comment:
                        print(f"⚠️ RACE AVOIDED: Found existing {comment_tag}. Skipping trade & Syncing.")
                        # It exists, so we just update our memory to match reality
                        self.current_step += 1
                        self.update_triggers_post_trade(direction)
                        self.save_state()
                        self.last_trade_time = time.time()
                        return

            # --- FIRE TRADE ---
            vol = self.get_volume(self.current_step)
            print(f"🚀 FIRING {direction.upper()} | Step {self.current_step + 1} | Lot: {vol}")
            
            if self.send_market_request_direct(direction, vol):
                # SUCCESS: Move Step Forward
                self.current_step += 1
                self.update_triggers_post_trade(direction)
                self.save_state()
                self.last_trade_time = time.time() # START COOL DOWN
            else:
                print("❌ Order Failed. Retrying next tick.")
                
        except Exception as e:
            print(f"❌ EXECUTION ERROR: {e}")
        finally:
            self.is_busy = False

    def update_triggers_post_trade(self, direction):
        # --- THE LOGIC PRESERVER ---
        # "if buy hits first, move sell to centre"
        # "if sell hits first move buy to centre"
        # "if crossing buy again, buy at the same price"
        
        if direction == "buy":
            if self.buy_trigger_name == "top":
                # Hit Top -> Enable Center Sell
                self.sell_trigger_name = "center"
                self.buy_trigger_name = None 
            elif self.buy_trigger_name == "center":
                # Hit Center -> Enable Bottom Sell
                self.sell_trigger_name = "bottom"
                self.buy_trigger_name = None
                
        elif direction == "sell":
            if self.sell_trigger_name == "bottom":
                # Hit Bottom -> Enable Center Buy
                self.buy_trigger_name = "center"
                self.sell_trigger_name = None
            elif self.sell_trigger_name == "center":
                # Hit Center -> Enable Top Buy
                self.buy_trigger_name = "top"
                self.sell_trigger_name = None

    def send_market_request_direct(self, direction, volume):
        symbol_info = mt5.symbol_info(self.symbol)
        if not symbol_info: return False
        
        point = symbol_info.point
        min_dist = (symbol_info.trade_stops_level * point) + (5 * point)
        
        tick = mt5.symbol_info_tick(self.symbol)
        price = tick.ask if direction == "buy" else tick.bid
        type_op = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        
        # --- CONSTANT SL/TP LOGIC ---
        if self.active_upper_level is not None and self.active_lower_level is not None:
            upper = self.active_upper_level
            lower = self.active_lower_level
        else:
            sl_cfg = float(self.config.get(f'{direction}_stop_sl', 0))
            tp_cfg = float(self.config.get(f'{direction}_stop_tp', 0))
            
            dist_sl = max(sl_cfg, min_dist) if sl_cfg > 0 else min_dist
            dist_tp = max(tp_cfg, min_dist) if tp_cfg > 0 else min_dist
            
            if direction == "buy":
                upper = price + dist_tp
                lower = price - dist_sl
            else:
                upper = price + dist_sl
                lower = price - dist_tp
                
            self.active_upper_level = upper
            self.active_lower_level = lower
            self.save_state()
            print(f"🔒 LOCKED LEVELS: Upper={upper:.5f}, Lower={lower:.5f}")

        if direction == "buy":
            tp = upper
            sl = lower
        else:
            sl = upper
            tp = lower

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(volume),
            "type": type_op,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": self.iteration,
            "comment": f"S{self.current_step}", # Used for IDEMPOTENCY check
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
            "deviation": 50
        }
        
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE and res.retcode != 10008:
            print(f"❌ Order Fail: {res.comment} ({res.retcode})")
            return False
        return True

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        if not step_lots: return 0.01
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1]

    def save_state(self):
        state = {
            "symbol": self.symbol,
            "anchor_center_ask": self.anchor_center_ask,
            "anchor_center_bid": self.anchor_center_bid,
            "anchor_top_ask": self.anchor_top_ask,
            "anchor_bottom_bid": self.anchor_bottom_bid,
            "buy_trigger_name": self.buy_trigger_name,
            "sell_trigger_name": self.sell_trigger_name,
            "active_upper_level": self.active_upper_level,
            "active_lower_level": self.active_lower_level,
            "current_step": self.current_step,
            "iteration": self.iteration
        }
        try:
            with open("bot_state.json", "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"⚠️ Failed to save state: {e}")

    def load_state(self):
        if os.path.exists("bot_state.json"):
            try:
                with open("bot_state.json", "r") as f:
                    state = json.load(f)
                    if state.get("symbol") == self.symbol:
                        self.anchor_center_ask = state.get("anchor_center_ask")
                        self.anchor_center_bid = state.get("anchor_center_bid")
                        self.anchor_top_ask = state.get("anchor_top_ask")
                        self.anchor_bottom_bid = state.get("anchor_bottom_bid")
                        self.buy_trigger_name = state.get("buy_trigger_name")
                        self.sell_trigger_name = state.get("sell_trigger_name")
                        self.active_upper_level = state.get("active_upper_level")
                        self.active_lower_level = state.get("active_lower_level")
                        self.current_step = state.get("current_step", 0)
                        self.iteration = state.get("iteration", 1)
            except: pass

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "open_positions": self.open_positions,
            "step": self.current_step,
            "iteration": self.iteration,
            "is_resetting": self.is_resetting,
            "anchor": self.anchor_center_ask, 
            "next_buy": self.buy_trigger_name,
            "next_sell": self.sell_trigger_name
        }