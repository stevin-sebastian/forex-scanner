# Forex Strategy Scanner

Automated forex trading signal scanner using the MentFX methodology.
Monitors pairs against configurable strategies and alerts via email when entry conditions are met.

## Setup

### 1. Install Python dependencies
```bash
cd forex_scanner
pip install -r requirements.txt
```

### 2. Get a Twelve Data API key
1. Go to https://twelvedata.com and create a free account
2. Copy your API key from the dashboard
3. Free tier allows ~800 requests/day — the scanner caches data for 10 minutes to stay within limits

### 3. Set up Gmail App Password
1. Enable 2-factor authentication on your Gmail account
2. Go to Google Account → Security → App Passwords
3. Create an app password for "Mail"
4. Use this 16-character password (not your Gmail password) in `.env`

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env with your actual keys
```

### 5. Run the scanner
```bash
python main.py
```

Open `dashboard/index.html` in your browser while the scanner is running.

## Testing the data fetcher
```bash
python -c "from core.data_fetcher import DataFetcher; df = DataFetcher(); print(df.get_candles('EUR/USD', '4H').head())"
```

## Adding a new strategy

Edit `config/strategies.yaml`. Copy the `mentfx_basic` block, give it a new unique key, and set `enabled: true`. No code changes needed.

Example structure:
```yaml
strategies:
  my_new_strategy:
    name: "My New Strategy"
    enabled: true
    environment_filter:
      suitable_for_long: true
    context_phase:
      timeframe: "4H"
      conditions:
        flush:
          type: price_below_all_emas
          emas: [10, 20]
          min_bars: 3
    entry_phase:
      timeframe: "30min"
      conditions:
        push:
          type: cross_above_emas
          emas: [10, 20]
          min_body_ratio: 0.5
    signal:
      buy_stop_price: peak_of_entry_push
```

## Condition types

| Type | Description |
|------|-------------|
| `price_below_all_emas` | Price closes below all specified EMAs for `min_bars` consecutive bars |
| `atr_contraction` | ATR shrinks to `threshold` fraction of its value at start of `lookback` window |
| `cross_above_emas` | Bullish candle closes above all specified EMAs with `min_body_ratio` body |
| `retest_ema_zone` | Price touches EMA zone within `tolerance_pips` after being above it |

## File structure

```
forex_scanner/
├── main.py              # Scheduler + entry point
├── config/
│   └── strategies.yaml  # All strategies (edit this, not the Python files)
├── core/
│   ├── data_fetcher.py  # Twelve Data API client
│   ├── environment.py   # 4H trend/MA analysis
│   ├── strategy_engine.py # YAML-driven condition evaluation
│   └── signals.py       # SQLite persistence
├── alerts/
│   └── email_alert.py   # Gmail SMTP alerts
├── api/
│   └── server.py        # Flask API
└── dashboard/
    └── index.html       # Live dashboard (open in browser)
```
