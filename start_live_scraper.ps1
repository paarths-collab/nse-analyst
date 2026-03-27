chcp 65001 > $null
$env:PYTHONUTF8 = "1"

# Start live scraper with 6-hour broadcast window and 30-second polling
.\.venv\Scripts\python.exe .\fillings.py --scrape-only --hours 6 --poll 30
