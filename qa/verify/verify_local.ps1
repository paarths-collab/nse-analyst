$ErrorActionPreference = 'Stop'

Write-Host "[1/5] Starting local Postgres + Redis stack..."
docker compose -f docker-compose.local.yml up -d

Write-Host "[2/5] Waiting for services to become healthy..."
$maxAttempts = 30
for ($i = 1; $i -le $maxAttempts; $i++) {
    $pgHealthy = docker inspect -f "{{.State.Health.Status}}" news_scrape_postgres 2>$null
    $redisHealthy = docker inspect -f "{{.State.Health.Status}}" news_scrape_redis 2>$null

    if ($pgHealthy -eq "healthy" -and $redisHealthy -eq "healthy") {
        Write-Host "Services are healthy."
        break
    }

    if ($i -eq $maxAttempts) {
        throw "Local services did not become healthy in time."
    }

    Start-Sleep -Seconds 2
}

Write-Host "[3/5] Bootstrapping schema + stream groups..."
$env:DATABASE_URL = "postgresql://news_user:news_pass@localhost:5433/news_scrape"
$env:REDIS_URL = "redis://localhost:6380/0"
python scripts/bootstrap_pipeline.py

Write-Host "[4/5] Running full validator..."
python scripts/validate_pipeline.py

Write-Host "[5/5] Done. Local pipeline verification complete."
Write-Host "To stop local stack: docker compose -f docker-compose.local.yml down"
