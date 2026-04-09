# Deployment & Implementation Guide

## Production Deployment

### Pre-Deployment Checklist

- [ ] Python 3.8+ installed and verified
- [ ] All dependencies in requirements.txt
- [ ] Java installed (for PDF processing)
- [ ] OpenDataLoader JAR obtained
- [ ] Sufficient disk space for outputs
- [ ] Network access configured
- [ ] Security measures in place

### Docker Deployment (Optional)

Create `Dockerfile`:

```dockerfile
FROM python:3.9-slim

# Install Java for PDF processing
RUN apt-get update && apt-get install -y default-jre
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t doc-processor .
docker run -p 8000:8000 -v /path/to/outputs:/app/outputs doc-processor
```

### System Service Setup (Linux)

Create `/etc/systemd/system/doc-processor.service`:

```ini
[Unit]
Description=Document Processing Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/document_app
Environment="PATH=/opt/venv/bin"
ExecStart=/opt/venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable doc-processor
sudo systemctl start doc-processor
sudo systemctl status doc-processor
```

### Nginx Reverse Proxy Configuration

```nginx
upstream app {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name documents.example.com;
    
    location / {
        proxy_pass http://app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts for large files
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
```

### Apache Configuration

```apache
<VirtualHost *:80>
    ServerName documents.example.com
    
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/
    
    # Large file handling
    LimitRequestFieldSize 16384
    LimitRequestBody 536870912
</VirtualHost>
```

## Configuration Management

### Environment Variables

Create `.env` file:

```bash
# Server
HOST=0.0.0.0
PORT=8000
WORKERS=4

# File limits
MAX_FILE_SIZE_MB=500
UPLOAD_PATH=/var/runs/doc-processor/uploads
OUTPUT_PATH=/var/runs/doc-processor/outputs

# PDF processing
OPENDATALOADER_JAR=/opt/opendataloader/odl.jar
OPENDATALOADER_MODE=LOCAL

# Security
API_KEY=your_api_key_here
ALLOW_CORS=false
```

Load in app:
```python
from dotenv import load_dotenv
import os

load_dotenv()
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE_MB', 500))
```

## Performance Tuning

### Worker Configuration

For high throughput:
```bash
# Number of workers = (2 × CPU cores) + 1
uvicorn app:app --workers 9  # For 4-core system
```

### Memory Optimization

- Use PyPy for faster CSV/TXT parsing
- Cache parsed documents if re-processing same files
- Implement output cleanup policy

### Disk I/O

- Place uploads on fast SSD
- Use separate disk for outputs if possible
- Monitor output directory size:
  ```bash
  du -sh document_app/outputs/
  ```

## Monitoring & Logging

### Application Logging

Update `app.py` to add logging:

```python
import logging
from logging.handlers import RotatingFileHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

handler = RotatingFileHandler(
    'app.log',
    maxBytes=10485760,
    backupCount=10
)
logger.addHandler(handler)
```

### Health Check Endpoint

Add to `app.py`:

```python
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "memory": psutil.virtual_memory().percent
    }
```

Check: `curl http://localhost:8000/health`

### Prometheus Metrics (Optional)

```python
from prometheus_client import Counter, Histogram, make_wsgi_app

process_counter = Counter(
    'processed_files_total',
    'Total files processed',
    ['format']
)

process_duration = Histogram(
    'process_duration_seconds',
    'Processing duration',
    ['format']
)
```

## Security Considerations

### 1. File Upload Security

```python
# In validators.py
BLOCKED_EXTENSIONS = {'.exe', '.sh', '.bat', '.cmd', '.ps1'}
BLOCKED_MIMETYPES = {'application/x-executable', 'application/x-msdownload'}

@staticmethod
def validate_file_security(file_path: str) -> Tuple[bool, Optional[str]]:
    path = Path(file_path)
    
    if path.suffix.lower() in BLOCKED_EXTENSIONS:
        return False, "File type not allowed"
    
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type in BLOCKED_MIMETYPES:
        return False, "File MIME type not allowed"
    
    return True, None
```

### 2. Input Sanitization

```python
# Prevent path traversal
safe_filename = secure_filename(uploaded_filename)  # werkzeug.utils

# Verify file location
if not str(upload_path).startswith(str(UPLOAD_DIR)):
    raise ValueError("Invalid upload path")
```

### 3. Output Access Control

```python
# Only download files from your session
@app.get("/api/download/{session_id}/{file_type}")
async def download_file(session_id: str, file_type: str, 
                       current_user: User = Depends(get_current_user)):
    # Verify session belongs to current user
    if not user_owns_session(current_user.id, session_id):
        raise HTTPException(status_code=403)
    
    # Rest of download logic...
```

### 4. Rate Limiting

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/process")
@limiter.limit("10/minute")
async def process_file(request: Request, file: UploadFile = File(...)):
    # Process file...
```

### 5. CORS Configuration

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Specific domains
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)
```

## Backup & Recovery

### Output Backup Strategy

```bash
# Daily backup
0 2 * * * tar -czf /backups/doc-output-$(date +\%Y\%m\%d).tar.gz \
  /path/to/document_app/outputs

# Weekly cleanup (keep 30 days)
0 3 * * 0 find /path/to/document_app/outputs -mtime +30 -delete
```

### Database Backup (if audit trail added)

```python
import sqlite3
from datetime import datetime, timedelta

def backup_processing_log():
    conn = sqlite3.connect('processing.db')
    backup_path = f"backups/processing-{datetime.now():%Y%m%d_%H%M%S}.db"
    conn.backup(sqlite3.connect(backup_path))
    conn.close()
```

## Scaling Strategies

### Horizontal Scaling

1. Multiple instances behind load balancer
2. Shared output directory (NFS/S3)
3. Session state in Redis (optional)

```python
# Load balancer config (Nginx)
upstream doc_processors {
    server instance1.local:8000;
    server instance2.local:8000;
    server instance3.local:8000;
}
```

### Vertical Scaling

- Increase worker processes: `--workers 16`
- Add more memory: Increase Python heap
- Use faster storage: SSD instead of HDD

### Asynchronous Processing

For very large files:

```python
from celery import Celery

celery_app = Celery('doc_processor')

@celery_app.task
def process_file_async(file_path: str) -> dict:
    return parse_by_format(file_path)

@app.post("/api/process-async")
async def process_file_async(file: UploadFile):
    task = process_file_async.delay(str(upload_path))
    return {"task_id": task.id, "status": "processing"}

@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    task = celery_app.AsyncResult(task_id)
    return {"status": task.state, "result": task.result}
```

## Maintenance Tasks

### Regular Maintenance

```bash
# Stop application
pkill -f "uvicorn app:app"

# Clear old uploads
find uploads/ -mtime +1 -delete

# Clear old temporary outputs (keep 7 days)
find outputs/ -mtime +7 -delete

# Restart
python app.py
```

### Database Cleanup (if logging sessions)

```python
def cleanup_old_sessions(days: int = 30):
    cutoff = datetime.utcnow() - timedelta(days=days)
    for session_dir in outputs_dir.glob('*'):
        if datetime.fromtimestamp(session_dir.stat().st_mtime) < cutoff:
            shutil.rmtree(session_dir)
```

## Troubleshooting Deployment

### High Memory Usage

**Symptom**: Process memory grows > 1GB

**Causes**:
- Large file parsing without streaming
- Memory leak in parser

**Solutions**:
```bash
# Monitor memory
top -p $(pgrep -f "uvicorn")

# Restart on memory threshold
# Add to cron: @hourly /path/to/restart_if_large.sh
```

### Slow PDF Processing

**Symptom**: PDF files take > 30 seconds

**Causes**:
- Large PDF file
- Scanned PDF requiring OCR
- OpenDataLoader not optimized

**Solutions**:
- Increase timeout in proxy config
- Add PDF size limit: `MAX_PDF_SIZE_MB=100`
- Switch to HYBRID mode for scanned docs

### High CPU Usage

**Symptom**: CPU constant at 100%

**Causes**:
- Too many concurrent requests
- Large file parsing loop

**Solutions**:
```python
# Add request queuing
@app.post("/api/process")
@limiter.limit("5/minute")  # Reduce concurrent
async def process_file(...):
```

## Upgrade Path

### Upgrading Dependencies

```bash
pip install --upgrade -r requirements.txt
# Test with sample files
python -m pytest tests/
# Restart service
```

### Version Management

Keep release notes:
```
v1.0.0 - Initial release
  - CSV, XLSX, DOCX, TXT parsers
  - Basic PDF support
  
v1.1.0 - PDF improvements
  - OpenDataLoader integration
  - Hybrid mode for scanned PDFs
  
v1.2.0 - MT940 support
  - Added banking format parser
  - Improved security
```

---

## Quick Deployment Commands

### Local Development
```bash
python app.py
```

### Production (Single Machine)
```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

### Production (Docker)
```bash
docker build -t document-processor .
docker run -d -p 8000:8000 -v /data/outputs:/app/outputs document-processor
```

### Production (Systemd Service)
```bash
sudo systemctl start doc-processor
sudo systemctl enable doc-processor
```
