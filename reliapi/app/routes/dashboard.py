"""Marketing Dashboard - веб-дашборд для мониторинга issues/постов.

Этот модуль предоставляет веб-интерфейс для мониторинга:
- GitHub issues и mentions
- Reddit постов и комментариев
- Twitter mentions
- Analytics метрики

Все данные фильтруются и экспортируются автоматически.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import redis

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


class DashboardItem(BaseModel):
    """Dashboard item model."""

    id: str
    platform: str  # github, reddit, twitter
    type: str  # issue, mention, post, comment
    title: str
    content: str
    url: str
    author: Optional[str] = None
    timestamp: datetime
    metadata: Dict = {}


class DashboardFilter(BaseModel):
    """Dashboard filter model."""

    platform: Optional[str] = None
    type: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    search: Optional[str] = None


@router.get("/", response_class=HTMLResponse)
async def dashboard_html():
    """Возвращает HTML дашборд."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <title>ReliAPI Marketing Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; }
        .filters { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        .filter { padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; }
        .items { display: grid; gap: 15px; }
        .item { border: 1px solid #ddd; padding: 15px; border-radius: 4px; }
        .item-header { display: flex; justify-content: space-between; margin-bottom: 10px; }
        .platform { padding: 4px 8px; border-radius: 4px; font-size: 12px; }
        .platform-github { background: #24292e; color: white; }
        .platform-reddit { background: #ff4500; color: white; }
        .platform-twitter { background: #1da1f2; color: white; }
        .export-btn { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .export-btn:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="container">
        <h1>ReliAPI Marketing Dashboard</h1>
        
        <div class="filters">
            <select id="platform" class="filter">
                <option value="">All Platforms</option>
                <option value="github">GitHub</option>
                <option value="reddit">Reddit</option>
                <option value="twitter">Twitter</option>
            </select>
            <select id="type" class="filter">
                <option value="">All Types</option>
                <option value="issue">Issues</option>
                <option value="mention">Mentions</option>
                <option value="post">Posts</option>
            </select>
            <input type="text" id="search" class="filter" placeholder="Search...">
            <button onclick="loadItems()" class="export-btn">Filter</button>
            <button onclick="exportCSV()" class="export-btn">Export CSV</button>
            <button onclick="exportJSON()" class="export-btn">Export JSON</button>
        </div>
        
        <div id="items" class="items"></div>
    </div>
    
    <script>
        async function loadItems() {
            const platform = document.getElementById('platform').value;
            const type = document.getElementById('type').value;
            const search = document.getElementById('search').value;
            
            const params = new URLSearchParams();
            if (platform) params.append('platform', platform);
            if (type) params.append('type', type);
            if (search) params.append('search', search);
            
            const response = await fetch(`/dashboard/api/items?${params}`);
            const items = await response.json();
            
            const itemsDiv = document.getElementById('items');
            itemsDiv.innerHTML = items.map(item => `
                <div class="item">
                    <div class="item-header">
                        <span class="platform platform-${item.platform}">${item.platform}</span>
                        <span>${new Date(item.timestamp).toLocaleString()}</span>
                    </div>
                    <h3><a href="${item.url}" target="_blank">${item.title}</a></h3>
                    <p>${item.content.substring(0, 200)}...</p>
                    ${item.author ? `<p><strong>Author:</strong> ${item.author}</p>` : ''}
                </div>
            `).join('');
        }
        
        async function exportCSV() {
            const response = await fetch('/dashboard/api/export?format=csv');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'dashboard-export.csv';
            a.click();
        }
        
        async function exportJSON() {
            const response = await fetch('/dashboard/api/export?format=json');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'dashboard-export.json';
            a.click();
        }
        
        loadItems();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html)


@router.get("/api/items")
async def get_items(
    platform: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
) -> List[Dict]:
    """Получает отфильтрованные items для дашборда."""
    
    # Получаем все items из Redis
    items = []
    
    # GitHub issues
    github_keys = redis_client.keys("github:issue:*")
    for key in github_keys:
        data = redis_client.get(key)
        if data:
            item = json.loads(data)
            items.append({
                "id": item.get("id"),
                "platform": "github",
                "type": "issue",
                "title": item.get("title", ""),
                "content": item.get("body", ""),
                "url": item.get("html_url", ""),
                "author": item.get("user", {}).get("login"),
                "timestamp": item.get("created_at", datetime.utcnow().isoformat()),
            })
    
    # Reddit posts
    reddit_keys = redis_client.keys("reddit:post:*")
    for key in reddit_keys:
        data = redis_client.get(key)
        if data:
            item = json.loads(data)
            items.append({
                "id": item.get("id"),
                "platform": "reddit",
                "type": "post",
                "title": item.get("title", ""),
                "content": item.get("selftext", ""),
                "url": f"https://reddit.com{item.get('permalink', '')}",
                "author": item.get("author"),
                "timestamp": datetime.fromtimestamp(item.get("created_utc", 0)).isoformat(),
            })
    
    # Применяем фильтры
    if platform:
        items = [i for i in items if i["platform"] == platform]
    
    if type:
        items = [i for i in items if i["type"] == type]
    
    if search:
        search_lower = search.lower()
        items = [
            i
            for i in items
            if search_lower in i["title"].lower() or search_lower in i["content"].lower()
        ]
    
    # Сортируем по дате (новые сначала)
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return items[:100]  # Ограничиваем 100 items


@router.get("/api/export")
async def export_items(
    format: str = Query("json", regex="^(json|csv)$"),
    platform: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
):
    """Экспортирует items в CSV или JSON."""
    
    items = await get_items(platform=platform, type=type)
    
    if format == "csv":
        import csv
        from io import StringIO
        
        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["id", "platform", "type", "title", "content", "url", "author", "timestamp"],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(item)
        
        return JSONResponse(
            content={"csv": output.getvalue()},
            headers={"Content-Type": "text/csv"},
        )
    
    else:  # JSON
        return JSONResponse(content=items)

