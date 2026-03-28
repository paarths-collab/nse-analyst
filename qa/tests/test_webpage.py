#!/usr/bin/env python3
import cloudscraper
from bs4 import BeautifulSoup
import re

scraper = cloudscraper.create_scraper()
HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://www.nseindia.com/',
    'Accept-Language': 'en-US,en;q=0.9'
}

url = 'https://www.nseindia.com/companies-listing/corporate-filings-announcements'
print(f"🌐 Fetching: {url}")

res = scraper.get(url, headers=HEADERS)
print(f"✓ Status: {res.status_code}")

soup = BeautifulSoup(res.content, 'html.parser')

tables = soup.find_all('table')
print(f"\n📋 Found {len(tables)} tables")

for table_idx, table in enumerate(tables):
    rows = table.find_all('tr')
    print(f"\nTable {table_idx}: {len(rows)} rows")
    
    # Print first few rows to understand structure
    for row_idx, row in enumerate(rows[:5]):
        cells = row.find_all('td')
        if cells:
            print(f"  Row {row_idx}:")
            for cell_idx, cell in enumerate(cells[:6]):
                text = cell.get_text(strip=True)[:40]
                print(f"    Col {cell_idx}: {text}")
        
        # Show raw HTML for first data row
        if row_idx == 1:
            print(f"\n  Raw HTML of row 1:")
            print(f"    {str(row)[:500]}")

# Try to find GAIL specifically
print("\n🔍 Searching for 'GAIL' in the page:")
gail_links = soup.find_all(string=re.compile('GAIL', re.IGNORECASE))
print(f"Found {len(gail_links)} references to GAIL")

gail_rows = soup.find_all('tr', string=re.compile('GAIL', re.IGNORECASE))
print(f"Found {len(gail_rows)} rows with GAIL")

# Find all cells with GAIL
for tag in soup.find_all(string=re.compile('GAIL', re.IGNORECASE)):
    parent = tag.parent
    if parent.name == 'td':
        row = parent.find_parent('tr')
        if row:
            cells = row.find_all('td')
            print(f"\nGAIL row found:")
            for i, cell in enumerate(cells[:6]):
                print(f"  Col {i}: {cell.get_text(strip=True)[:50]}")
