"""Module for Quotex websocket."""
import json
import time
import logging
import websocket
import threading
from typing import Dict, List, Optional, Union, Callable
import cloudscraper
from urllib.parse import urlparse
from .. import global_value

logger = logging.getLogger(__name__)


class WebsocketClient(object):
    """Class for work with Quotex API websocket."""

    def __init__(self, api):
        """
        :param api: The instance of :class:`QuotexAPI
            <quotexapi.api.QuotexAPI>`.
        """
        self.api = api
        self.websocket_client: Optional[websocket.WebSocketApp] = None
        self.websocket_thread: Optional[threading.Thread] = None
        self.reconnect_count = 0
        self.max_reconnects = 5
        self.connected = threading.Event()
        self.headers = {}
        self.scraper = cloudscraper.create_scraper()

    def _prepare_headers(self):
        """Prepare headers with Cloudflare tokens"""
        try:
            # Get the domain from the WebSocket URL
            parsed = urlparse(self.api.wss_url)
            domain = f"https://{parsed.netloc}"
            
            # Get Cloudflare tokens
            tokens = self.scraper.get(domain).cookies.get_dict()
            
        self.headers = {
            "User-Agent": self.api.session_data.get("user_agent"),
                "Origin": domain,
            "Host": f"ws2.{self.api.host}",
                "Cookie": "; ".join([f"{k}={v}" for k, v in tokens.items()]),
            }
            logger.debug("Prepared headers with Cloudflare tokens")
        except Exception as e:
            logger.error(f"Error preparing headers: {str(e)}")
            raise

    def connect(self):
        """Connect to Quotex WebSocket."""
        self._prepare_headers()
        logger.debug("Initiating WebSocket connection")

        websocket.enableTrace(self.api.trace_ws)
        self.websocket_client = websocket.WebSocketApp(
            self.api.wss_url,
            header=self.headers,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )

        self.websocket_thread = threading.Thread(
            target=self.websocket_client.run_forever,
            kwargs={
                'ping_interval': 30,
                'ping_timeout': 10,
                'ping_payload': 'ping',
                'skip_utf8_validation': True
            }
        )
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        
        # Wait for connection or timeout
        if not self.connected.wait(timeout=30):
            logger.error("WebSocket connection timeout")
            self.reconnect()
            return False
        
        return True

    def reconnect(self):
        """Reconnect to Quotex WebSocket."""
        if self.reconnect_count >= self.max_reconnects:
            logger.error("Max reconnection attempts reached")
            return False
            
        self.reconnect_count += 1
        logger.info(f"Attempting reconnection {self.reconnect_count}/{self.max_reconnects}")
        
        if self.websocket_client:
            self.websocket_client.close()
            
        time.sleep(min(self.reconnect_count * 2, 10))  # Exponential backoff
        return self.connect()

    def on_message(self, ws, message):
        """Called when message received from Quotex WebSocket."""
        global_value.ssl_Mutual_exclusion = True
        current_time = time.localtime()
        if current_time.tm_sec in [0, 5, 10, 15, 20, 30, 40, 50]:
            self.websocket_client.send('42["tick"]')
        try:
            if "authorization/reject" in str(message):
                print("Token rejected, making automatic reconnection.")
                logger.debug("Token rejected, making automatic reconnection.")
                global_value.check_rejected_connection = 1
            elif "s_authorization" in str(message):
                global_value.check_accepted_connection = 1
                global_value.check_rejected_connection = 0
            elif "instruments/list" in str(message):
                global_value.started_listen_instruments = True

            try:
                logger.debug(f"Received message: {message[:200]}...")  # Log truncated message
                decoded_message = message.decode('utf-8')
                message = json.loads(decoded_message)
                self.api.wss_message = message
                if "call" in str(message) or 'put' in str(message):
                    self.api.instruments = message
                if isinstance(message, dict):
                    if message.get("signals"):
                        time_in = message.get("time")
                        for i in message["signals"]:
                            try:
                                self.api.signal_data[i[0]] = {}
                                self.api.signal_data[i[0]][i[2]] = {}
                                self.api.signal_data[i[0]][i[2]]["dir"] = i[1][0]["signal"]
                                self.api.signal_data[i[0]][i[2]]["duration"] = i[1][0]["timeFrame"]
                            except:
                                self.api.signal_data[i[0]] = {}
                                self.api.signal_data[i[0]][time_in] = {}
                                self.api.signal_data[i[0]][time_in]["dir"] = i[1][0][1]
                                self.api.signal_data[i[0]][time_in]["duration"] = i[1][0][0]
                    elif message.get("liveBalance") or message.get("demoBalance"):
                        self.api.account_balance = message
                    elif message.get("position"):
                        self.api.top_list_leader = message
                    elif len(message) == 1 and message.get("profit", -1) > -1:
                        self.api.profit_today = message
                    elif message.get("index"):
                        self.api.historical_candles = message
                        self.api.timesync.server_timestamp = message.get("closeTimestamp")
                    if message.get("pending"):
                        self.api.pending_successful = message
                        self.api.pending_id = message["pending"]["ticket"]
                    elif message.get("id") and not message.get("ticket"):
                        self.api.buy_successful = message
                        self.api.buy_id = message["id"]
                        self.api.timesync.server_timestamp = message.get("closeTimestamp")
                    elif message.get("ticket") and not message.get("id"):
                        self.api.sold_options_respond = message
                    elif message.get("deals"):
                        for get_m in message["deals"]:
                            self.api.profit_in_operation = get_m["profit"]
                            get_m["win"] = True if message["profit"] > 0 else False
                            get_m["game_state"] = 1
                            self.api.listinfodata.set(
                                get_m["win"],
                                get_m["game_state"],
                                get_m["id"]
                            )
                    elif message.get("isDemo") and message.get("balance"):
                        self.api.training_balance_edit_request = message
                    elif message.get("error"):
                        global_value.websocket_error_reason = message.get("error")
                        global_value.check_websocket_if_error = True
                        if global_value.websocket_error_reason == "not_money":
                            self.api.account_balance = {"liveBalance": 0}
                    elif not message.get("list") == []:
                        self.api.wss_message = message
            except:
                pass

            if str(message) == "41":
                logger.info("Disconnection event triggered by the platform, causing automatic reconnection.")
                global_value.check_websocket_if_connect = 0
            if "51-" in str(message):
                self.api._temp_status = str(message)
            elif self.api._temp_status == """451-["settings/list",{"_placeholder":true,"num":0}]""":
                self.api.settings_list = message
                self.api._temp_status = ""
            elif self.api._temp_status == """451-["history/list/v2",{"_placeholder":true,"num":0}]""":
                if message.get("asset") == self.api.current_asset:
                    self.api.candles.candles_data = message["history"]
                    self.api.candle_v2_data[message["asset"]] = message
                    self.api.candle_v2_data[message["asset"]]["candles"] = [{
                        "time": candle[0],
                        "open": candle[1],
                        "close": candle[2],
                        "high": candle[3],
                        "low": candle[4],
                        "ticks": candle[5]
                    } for candle in message["candles"]]
            elif len(message[0]) == 4:
                result = {
                    "time": message[0][1],
                    "price": message[0][2]
                }
                self.api.realtime_price[message[0][0]].append(result)
                self.api.realtime_candles = message[0]
            elif len(message[0]) == 2:
                for i in message:
                    result = {
                        "sentiment": {
                            "sell": 100 - int(i[1]),
                            "buy": int(i[1])
                        }
                    }
                    self.api.realtime_sentiment[i[0]] = result
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
        global_value.ssl_Mutual_exclusion = False

    def on_error(self, ws, error):
        """Called when error occurred from Quotex WebSocket."""
        if isinstance(error, websocket.WebSocketBadStatusException):
            if error.status_code == 403:
                logger.error(f"Cloudflare protection triggered: {error}")
                self._prepare_headers()  # Refresh Cloudflare tokens
            else:
                logger.error(f"WebSocket error: {error}")
        else:
            logger.error(f"WebSocket error: {error}")
        
        self.connected.clear()
        self.reconnect()

    def on_open(self, ws):
        """Called when Quotex WebSocket connection opened."""
        logger.info("WebSocket connection established")
        self.reconnect_count = 0
        self.connected.set()
        asset_name = self.api.current_asset
        period = self.api.current_period
        self.websocket_client.send('42["tick"]')
        self.websocket_client.send('42["indicator/list"]')
        self.websocket_client.send('42["drawing/load"]')
        self.websocket_client.send('42["pending/list"]')
        self.websocket_client.send('42["instruments/update",{"asset":"%s","period":%d}]' % (asset_name, period))
        self.websocket_client.send('42["depth/follow","%s"]' % asset_name)
        self.websocket_client.send('42["chart_notification/get"]')
        self.websocket_client.send('42["tick"]')

    def on_close(self, ws, close_status_code, close_msg):
        """Called when Quotex WebSocket connection closed."""
        logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
        self.connected.clear()
        if close_status_code != 1000:  # Not a normal closure
            self.reconnect()

    def send(self, data):
        """Send data to Quotex WebSocket server.
        
        :param data: The instance of :class:`websocket.WebSocket`.
        """
        try:
            if not self.websocket_client or not self.websocket_client.sock:
                logger.warning("WebSocket not connected, attempting reconnection")
                if not self.reconnect():
                    raise Exception("Failed to reconnect WebSocket")
                    
            self.websocket_client.send(data)
            logger.debug(f"Sent data: {data[:200]}...")  # Log truncated data
            
        except Exception as e:
            logger.error(f"Error sending data: {str(e)}")
            self.reconnect()
