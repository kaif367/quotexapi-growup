# Quotex Data API

A FastAPI-based REST API for retrieving real-time and historical data from Quotex trading platform.

## Features

- Real-time candle data with IST timestamps
- Last 200 candles for any trading pair
- Payment and payout information
- Automatic data caching
- CORS enabled
- Railway deployment ready

## API Endpoints

### 1. Get Candle Data
```http
GET /candles/{pair}/{period}
```
- `pair`: Trading pair (e.g., EURUSD_otc)
- `period`: Candle period in seconds [5,10,15,30,60,120,180,240,300,600,900,1800,3600,14400,86400]
- Optional: `force_refresh=true` query parameter

### 2. Get Payment Information
```http
GET /payments
```
Returns payment information and profit percentages for all assets.

### 3. Get Asset Payout
```http
GET /payout/{asset}
```
Returns payout information for a specific asset.

### 4. Cache Management
```http
GET /cache/clear
```
Clears all API caches.

### 5. Health Check
```http
GET /health
```
Returns API health status.

## Installation

1. Clone the repository:
```bash
git clone https://github.com/kaif367/quotexdataapi.git
cd quotexdataapi
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the API locally:
```bash
python api_server.py
```

## Railway Deployment

This API is configured for Railway deployment. Required files:
- `Procfile`: Defines process types
- `requirements.txt`: Python dependencies
- `runtime.txt`: Python version specification

## Environment Variables

Required environment variables:
- `PORT`: Set automatically by Railway
- `QUOTEX_EMAIL`: Your Quotex account email
- `QUOTEX_PASSWORD`: Your Quotex account password

## Response Format

Example candle data response:
```json
{
  "pair": "EURUSD_otc",
  "period": 60,
  "candle_count": 200,
  "candles": [
    {
      "symbol_id": 66,
      "time": 1751043720,
      "time_ist": "2025-06-29 23:49:20 IST",
      "open": 1.1627,
      "close": 1.16256,
      "high": 1.16277,
      "low": 1.1625,
      "ticks": 78,
      "last_tick": 1751043779.675,
      "last_tick_ist": "2025-06-29 23:49:39 IST",
      "asset": "EURUSD_otc"
    }
  ],
  "cached": false,
  "timestamp": "2025-06-29 23:49:10 IST"
}
```

## Cache Configuration

- Default cache duration: 60 seconds
- Force refresh available for all endpoints
- Automatic cache invalidation for stale data

## Error Handling

The API returns appropriate HTTP status codes:
- 200: Successful request
- 400: Invalid parameters
- 404: Data not found
- 500: Server error

## Contributing

Feel free to open issues and pull requests for improvements.

## License

MIT License
