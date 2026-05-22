from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import uuid
import datetime
import subprocess
import re
import os
import sys
from typing import Dict, Any, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from fastapi import Depends

# Import database models
from api.database import SessionLocal, JobRecord, get_db

app = FastAPI(
    title="PSUR Agent OS API",
    description="SOTA Agent OS for automated medical device PSUR generation.",
    version="1.0.0"
)

# Add CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In strict production, lock this down to the frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GenerationRequest(BaseModel):
    product_name: Optional[str] = ""
    reporting_period: Optional[str] = ""
    target_markets: list[str] = ["EU", "US"]
    include_uk_mdr: bool = False

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    created_at: str
    completed_at: Optional[str] = None
    document_url: Optional[str] = None

def run_psur_generation_task(job_id: str, request: GenerationRequest):
    """Background task to run the actual PSUR generation pipeline via subprocess."""
    db = SessionLocal()
    job = db.query(JobRecord).filter(JobRecord.id == job_id).first()
    if not job:
        db.close()
        return

    try:
        job.status = "in_progress"
        job.progress = 0.05
        db.commit()
        
        # Parse dates (assume standard 1-year period ending today if not provided in exact format)
        import datetime
        end_date = datetime.datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        
        if "-" in request.reporting_period and len(request.reporting_period.split("-")) == 2:
            parts = request.reporting_period.split("-")
            if len(parts[0]) == 4: # e.g. 2023-2024
                start_date = f"{parts[0]}-01-01"
                end_date = f"{parts[1]}-12-31"

        # Build command
        cmd = [
            sys.executable, "main.py", "generate",
            request.product_name or "",
            "--start", start_date,
            "--end", end_date
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1, # Line buffered
            universal_newlines=True
        )
        
        completed_sections = 0
        total_sections = 13 # A to M
        final_docx_path = None
        
        for line in process.stdout:
            # Print the line to the server console so the user can see it!
            sys.stdout.write(line)
            sys.stdout.flush()
            
            # Look for progress indicators
            if "Generating PSUR sections" in line:
                job.progress = 0.1
                db.commit()
                
            elif "(done)" in line and "Section" in line:
                completed_sections += 1
                job.progress = 0.1 + (0.8 * (completed_sections / total_sections))
                db.commit()
                
            elif "DOCX (template-based):" in line:
                match = re.search(r"DOCX \(template-based\):\s*(.+)", line)
                if match:
                    final_docx_path = match.group(1).strip()
                    
        process.wait()
        
        if process.returncode != 0:
            raise Exception(f"Pipeline failed with exit code {process.returncode}")
            
        job.progress = 1.0
        job.status = "completed"
        job.completed_at = datetime.datetime.utcnow()
        job.document_url = f"/api/v1/documents/{job_id}"
        
        if final_docx_path:
            job.local_file_path = final_docx_path
            
        db.commit()
        
    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        db.commit()
    finally:
        db.close()


@app.post("/api/v1/generate", response_model=JobStatusResponse)
async def generate_psur(request: GenerationRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Submit a new PSUR generation job to the Agent OS."""
    job_id = str(uuid.uuid4())
    
    new_job = JobRecord(
        id=job_id,
        status="queued",
        progress=0.0,
        request_data=request.model_dump()
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    
    background_tasks.add_task(run_psur_generation_task, job_id, request)
    
    return JobStatusResponse(
        job_id=new_job.id,
        status=new_job.status,
        progress=new_job.progress,
        created_at=new_job.created_at.isoformat()
    )


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """Poll the status of a specific PSUR generation job."""
    job = db.query(JobRecord).filter(JobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        document_url=job.document_url
    )


@app.get("/api/v1/documents/{job_id}")
async def download_document(job_id: str, db: Session = Depends(get_db)):
    """Download the finalized PSUR DOCX file."""
    job = db.query(JobRecord).filter(JobRecord.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    if not job.local_file_path or not os.path.exists(job.local_file_path):
        raise HTTPException(status_code=404, detail="Document not found or generation failed")
        
    return FileResponse(
        path=job.local_file_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(job.local_file_path)
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "psur-agent-os"}
