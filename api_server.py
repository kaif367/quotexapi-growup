from fastapi import FastAPI, Query, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from typing import Optional, Dict, List
import time
import asyncio
import os
import logging
from datetime import datetime, timedelta
import pytz
from quotexapi.stable_api import Quotex
from quotexapi.expiration import get_timestamp_days_ago, timestamp_to_date
from quotexapi.config import credentials

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get port from environment variable (Railway sets this)
PORT = int(os.getenv("PORT", 8000))

app = FastAPI(
    title="Quotex Candle Data API",
    description="API to get progressive candle data from Quotex platform",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
MAX_CANDLES = 200
VALID_PERIODS = [5, 10, 15, 30, 60, 120, 180, 240, 300, 600, 900, 1800, 3600, 14400, 86400]

# Cache configuration
CACHE_DURATION = 60  # Cache duration in seconds
candle_cache: Dict = {}
payment_cache: Dict = {}
last_connection_time = None
connection_timeout = 1800  # 30 minutes

# Timezone configuration
ist_tz = pytz.timezone('Asia/Kolkata')

# Global client instance
client = None
connection_lock = asyncio.Lock()

def convert_to_ist(unix_timestamp):
    """Convert Unix timestamp to IST datetime string"""
    try:
        utc_dt = datetime.fromtimestamp(unix_timestamp, pytz.UTC)
        ist_dt = utc_dt.astimezone(ist_tz)
        return {
            "timestamp": unix_timestamp,
            "ist_time": ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")
        }
    except Exception as e:
        logger.error(f"Error converting timestamp {unix_timestamp}: {str(e)}")
        return {
            "timestamp": unix_timestamp,
            "ist_time": "Invalid timestamp"
        }

def process_candle_data(candle: dict) -> dict:
    """Process individual candle data to add IST time"""
    try:
        time_data = convert_to_ist(candle["time"])
        last_tick_data = convert_to_ist(float(candle["last_tick"]))
        
        return {
            **candle,
            "time": time_data["timestamp"],
            "time_ist": time_data["ist_time"],
            "last_tick": last_tick_data["timestamp"],
            "last_tick_ist": last_tick_data["ist_time"]
        }
    except Exception as e:
        logger.error(f"Error processing candle data: {str(e)}, candle: {candle}")
        raise

def should_refresh_cache(cache_entry: dict, current_time: int) -> bool:
    """Determine if cache should be refreshed based on last candle time"""
    if not cache_entry or 'data' not in cache_entry:
        return True
        
    try:
        candles = cache_entry['data'].get('candles', [])
        if not candles:
            return True
            
        last_candle = candles[-1]
        last_candle_time = last_candle.get('time', 0)
        
        # Refresh if last candle is more than 60 seconds old
        return (current_time - last_candle_time) > 60
    except Exception:
        return True

async def initialize_client():
    """Initialize and connect the Quotex client"""
    global client
    
    logger.info("Starting client initialization")
    try:
        # Get credentials
        email, password = credentials()
        logger.debug(f"Got credentials for email: {email}")
        
        # Initialize Quotex client
        client = Quotex(
            email=email,
            password=password,
            lang="en"
        )
        logger.debug("Quotex client instance created")
        
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                logger.info(f"Attempting to connect (attempt {retry_count + 1}/{max_retries})")
                check_connect, reason = await client.connect()
                if check_connect:
                    logger.info("Successfully connected to Quotex")
                    return True
                logger.warning(f"Connection attempt {retry_count + 1} failed: {reason}")
                retry_count += 1
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error during connection attempt {retry_count + 1}: {str(e)}")
                retry_count += 1
                await asyncio.sleep(5)
        
        raise Exception("Failed to establish connection after maximum retries")
    except Exception as e:
        logger.error(f"Error in initialize_client: {str(e)}")
        raise

@app.on_event("startup")
async def startup_event():
    """Initialize the Quotex client on application startup"""
    logger.info("Application startup event triggered")
    try:
        await initialize_client()
    except Exception as e:
        logger.error(f"Failed to initialize client during startup: {str(e)}")
        # Don't exit, let the application start and retry connection when needed

async def ensure_connection():
    """Ensure connection is active"""
    global client
    
    logger.debug("Checking connection status")
    async with connection_lock:
        try:
            if client is None:
                logger.info("Client not initialized, initializing now")
                await initialize_client()
            else:
                try:
                    logger.debug("Testing existing connection")
                    await client.get_payment()
                    logger.debug("Connection test successful")
                except Exception as e:
                    logger.warning(f"Connection test failed: {str(e)}, attempting reconnection")
                    await initialize_client()
        except Exception as e:
            logger.error(f"Error in ensure_connection: {str(e)}")
            raise

async def validate_asset(asset: str):
    """Validate if asset exists and is available"""
    logger.debug(f"Validating asset: {asset}")
    try:
        asset_name, asset_data = await client.get_available_asset(asset, force_open=True)
        if not asset_data or len(asset_data) < 3:
            logger.warning(f"Invalid asset data received for {asset}")
            raise HTTPException(status_code=400, detail=f"Invalid asset data for {asset}")
        if not asset_data[2]:
            logger.warning(f"Asset {asset} is currently closed")
            raise HTTPException(status_code=400, detail=f"Asset {asset} is currently closed")
        logger.debug(f"Asset {asset} validated successfully")
        return asset_name, asset_data
    except Exception as e:
        logger.error(f"Error validating asset {asset}: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Error validating asset {asset}: {str(e)}")

async def fetch_candles_batch(asset: str, end_from_time: int, offset: int, period: int, batch_size: int = 5) -> List[dict]:
    """Fetch candles in batches for better performance"""
    tasks = []
    for i in range(batch_size):
        current_time = end_from_time + (i * offset)
        tasks.append(client.get_candles(asset, current_time, offset, period, progressive=True))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    candles = []
    for result in results:
        if isinstance(result, Exception):
            print(f"Error fetching batch: {str(result)}")
            continue
        if result:
            candles.extend(result)
    return candles

@app.get("/candles/{pair}/{period}")
async def get_candles(
    pair: str = Path(..., description="Trading pair (e.g., EURUSD_otc)"),
    period: int = Path(..., description="Candle period in seconds"),
    force_refresh: bool = Query(False, description="Force refresh data ignoring cache")
):
    """
    Get last 200 candles for a specific trading pair and period
    """
    try:
        # Validate period
        if period not in VALID_PERIODS:
            raise HTTPException(status_code=400, detail=f"Invalid period. Must be one of {VALID_PERIODS}")

        # Create cache key
        cache_key = f"{pair}_{period}"
        current_time = int(time.time())
        
        # Check cache first if not forcing refresh
        if not force_refresh:
            cache_entry = candle_cache.get(cache_key)
            if cache_entry and not should_refresh_cache(cache_entry, current_time):
                return cache_entry['data']

        # Ensure connection is active
        await ensure_connection()

        # Validate asset
        asset_name, asset_data = await validate_asset(pair)

        # Calculate parameters for last 200 candles
        list_candles = []
        offset = period * 200  # Offset to get last 200 candles
        end_from_time = current_time
        
        # Get candles data in batches with retries
        max_retries = 3
        batch_size = 5
        
        for i in range(0, 5):  # 5 batches of 40 candles each to ensure we get enough data
            retry_count = 0
            while retry_count < max_retries:
                try:
                    batch_candles = await fetch_candles_batch(
                        pair, 
                        end_from_time - (i * offset), 
                        offset, 
                        period,
                        batch_size
                    )
                    if batch_candles:
                        list_candles.extend(batch_candles)
                        break
                    retry_count += 1
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"Error in batch {i}: {str(e)}")
                    retry_count += 1
                    await asyncio.sleep(1)

        if not list_candles:
            raise HTTPException(
                status_code=404,
                detail=f"No candle data available for {pair} with period {period}"
            )

        # Remove duplicates and sort by time
        clean_list = list({frozenset(d.items()): d for d in list_candles}.values())
        clean_list.sort(key=lambda x: x['time'], reverse=True)
        
        # Take only the last 200 candles
        clean_list = clean_list[:MAX_CANDLES]
        
        # Process each candle to add IST time
        processed_candles = [process_candle_data(candle) for candle in clean_list]
        
        # Prepare response
        response_data = {
            "pair": pair,
            "period": period,
            "candle_count": len(processed_candles),
            "candles": processed_candles,
            "cached": False,
            "timestamp": datetime.now(ist_tz).strftime("%Y-%m-%d %H:%M:%S IST")
        }

        # Update cache
        if processed_candles:
            candle_cache[cache_key] = {
                'data': response_data,
                'timestamp': current_time
            }
        
        return response_data

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/payments")
async def get_payments(force_refresh: bool = Query(False, description="Force refresh data ignoring cache")):
    """
    Get payment information for all assets including profit percentages
    """
    try:
        cache_key = "payments"
        current_time = int(time.time())
        
        # Check cache first if not forcing refresh
        if not force_refresh and cache_key in payment_cache:
            cache_entry = payment_cache[cache_key]
            if current_time - cache_entry['timestamp'] < CACHE_DURATION:
                return cache_entry['data']

        # Ensure connection is active
        await ensure_connection()

        # Get payment data
        all_data = client.get_payment()
        
        # Process payment data
        processed_data = {}
        for asset_name in all_data:
            asset_data = all_data[asset_name]
            processed_data[asset_name] = {
                "status": "open" if asset_data["open"] else "closed",
                "profit": {
                    "1_minute": asset_data["profit"]["1M"],
                    "5_minutes": asset_data["profit"]["5M"]
                }
            }

        # Prepare response
        response_data = {
            "payments": processed_data,
            "timestamp": datetime.now(ist_tz).strftime("%Y-%m-%d %H:%M:%S IST"),
            "cached": False
        }

        # Update cache
        payment_cache[cache_key] = {
            'data': response_data,
            'timestamp': current_time
        }
        
        return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching payment data: {str(e)}")

@app.get("/payout/{asset}")
async def get_payout_by_asset(
    asset: str = Path(..., description="Asset name (e.g., EURUSD_otc)"),
    force_refresh: bool = Query(False, description="Force refresh data ignoring cache")
):
    """
    Get payout information for a specific asset
    """
    try:
        cache_key = f"payout_{asset}"
        current_time = int(time.time())
        
        # Check cache first if not forcing refresh
        if not force_refresh and cache_key in payment_cache:
            cache_entry = payment_cache[cache_key]
            if current_time - cache_entry['timestamp'] < CACHE_DURATION:
                return cache_entry['data']

        # Ensure connection is active
        await ensure_connection()

        # Get payout data
        payout_data = client.get_payout_by_asset(asset)
        
        # Prepare response
        response_data = {
            "asset": asset,
            "payout": payout_data,
            "timestamp": datetime.now(ist_tz).strftime("%Y-%m-%d %H:%M:%S IST"),
            "cached": False
        }

        # Update cache
        payment_cache[cache_key] = {
            'data': response_data,
            'timestamp': current_time
        }
        
        return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching payout data for {asset}: {str(e)}")

@app.get("/cache/clear")
async def clear_cache():
    """Clear all API caches"""
    global candle_cache, payment_cache
    candle_cache = {}
    payment_cache = {}
    return {"message": "All caches cleared successfully"}

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(ist_tz).strftime("%Y-%m-%d %H:%M:%S IST")
    }

@app.get("/")
async def root():
    """Root endpoint that returns API information"""
    return {
        "name": "Quotex Candle Data API",
        "version": "1.0.0",
        "description": "API to get progressive candle data from Quotex platform",
        "status": "running",
        "endpoints": {
            "/candles/{pair}/{period}": "Get last 200 candles for a specific pair and period",
            "/payments": "Get payment information for all assets",
            "/payout/{asset}": "Get payout information for a specific asset",
            "/cache/clear": "Clear all API caches",
            "/health": "Health check endpoint"
        },
        "valid_periods": VALID_PERIODS
    }

if __name__ == "__main__":
    # Use PORT from environment variable when running on Railway
    uvicorn.run("api_server:app", host="0.0.0.0", port=PORT, reload=True) 