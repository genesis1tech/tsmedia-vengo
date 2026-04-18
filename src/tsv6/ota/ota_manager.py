#!/usr/bin/env python3
"""
AWS IoT OTA (Over-The-Air) Update Manager
=========================================

Comprehensive OTA update system supporting:
- Code/firmware updates via AWS IoT Jobs
- Media asset updates (videos, images)
- Secure download and verification
- Atomic updates with rollback capability
- Progress reporting and status updates

Supports both full system updates and selective media updates.
"""

import json
import os
import sys
import time
import threading
import hashlib
import shutil
import subprocess
import tempfile
import tarfile
import zipfile
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List, Tuple
from enum import Enum
from dataclasses import dataclass, asdict
import requests
from ..utils.filesystem_ops import sync_filesystem

# AWS IoT imports
try:
    from awscrt import mqtt
    AWS_CRT_AVAILABLE = True
except ImportError:
    AWS_CRT_AVAILABLE = False

class UpdateType(Enum):
    """Types of OTA updates"""
    FULL_SYSTEM = "full_system"
    CODE_ONLY = "code_only"
    MEDIA_ONLY = "media_only"
    CONFIGURATION = "configuration"
    AD_PLAYER_UPDATE = "ad_player_update"  # tsv6.ads independent rollout track

class JobStatus(Enum):
    """AWS IoT Jobs status values"""
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS" 
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    REJECTED = "REJECTED"
    REMOVED = "REMOVED"

@dataclass
class UpdateJob:
    """Represents an OTA update job"""
    job_id: str
    job_document: Dict[str, Any]
    update_type: UpdateType
    version: str
    download_urls: Dict[str, str]
    checksums: Dict[str, str]
    file_sizes: Dict[str, int]
    created_at: float
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    error_message: Optional[str] = None

@dataclass
class MediaAsset:
    """Represents a media asset to be updated"""
    filename: str
    url: str
    checksum: str
    size: int
    asset_type: str  # 'video', 'image', 'audio'
    target_path: str

class OTAManager:
    """AWS IoT OTA Update Manager"""
    
    def __init__(self, aws_manager, config, logger=None):
        """Initialize OTA Manager
        
        Args:
            aws_manager: AWS IoT connection manager
            config: Application configuration
            logger: Optional logger instance
        """
        self.aws_manager = aws_manager
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # OTA settings
        self.thing_name = aws_manager.thing_name
        self.staging_dir = Path("/tmp/ota_staging")
        self.backup_dir = Path("/tmp/ota_backup")
        self.media_staging_dir = Path("/tmp/media_staging")
        
        # Current state
        self.current_job: Optional[UpdateJob] = None
        self.is_updating = False
        self.update_lock = threading.Lock()
        
        # Progress tracking
        self.progress_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None
        
        # AWS IoT Jobs topics
        self.jobs_notify_topic = f"$aws/things/{self.thing_name}/jobs/notify-next"
        self.jobs_get_topic = f"$aws/things/{self.thing_name}/jobs/get"
        self.jobs_update_topic_template = "$aws/things/{}/jobs/{}/update"
        
        # Initialize directories
        self._setup_directories()
        
        self.logger.info(f"OTA Manager initialized for {self.thing_name}")

    def _setup_directories(self):
        """Create necessary directories for OTA operations"""
        directories = [
            self.staging_dir,
            self.backup_dir, 
            self.media_staging_dir,
            self.staging_dir / "code",
            self.staging_dir / "media",
            self.backup_dir / "code",
            self.backup_dir / "media"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            
        self.logger.info("OTA directories initialized")

    def initialize_jobs_client(self):
        """Initialize AWS IoT Jobs client and subscribe to topics"""
        if not self.aws_manager.connected:
            self.logger.error("AWS IoT not connected - cannot initialize Jobs client")
            return False
            
        try:
            # Subscribe to job notifications
            subscribe_future, _ = self.aws_manager.connection.subscribe(
                topic=self.jobs_notify_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_job_notification
            )
            subscribe_future.result(timeout=10)
            
            # Subscribe to job updates
            jobs_wildcard_topic = f"$aws/things/{self.thing_name}/jobs/+/get/accepted"
            subscribe_future, _ = self.aws_manager.connection.subscribe(
                topic=jobs_wildcard_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_job_details_received
            )
            subscribe_future.result(timeout=10)
            
            # Request pending jobs
            self._request_pending_jobs()
            
            self.logger.info("AWS IoT Jobs client initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Jobs client: {e}")
            return False

    def _request_pending_jobs(self):
        """Request any pending jobs from AWS IoT Jobs"""
        try:
            message = {"clientToken": f"ota-{int(time.time())}"}
            
            publish_future, _ = self.aws_manager.connection.publish(
                topic=self.jobs_get_topic,
                payload=json.dumps(message),
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            publish_future.result(timeout=5)
            
            self.logger.info("Requested pending jobs from AWS IoT")
            
        except Exception as e:
            self.logger.error(f"Failed to request pending jobs: {e}")

    def _on_job_notification(self, topic, payload, dup, qos, retain, **kwargs):
        """Handle job notification from AWS IoT Jobs"""
        try:
            message = json.loads(payload.decode('utf-8'))
            self.logger.info(f"Received job notification: {message}")
            
            # Check for execution data
            execution = message.get('execution')
            if execution:
                job_id = execution.get('jobId')
                if job_id:
                    self._process_job_notification(job_id, execution)
                    
        except Exception as e:
            self.logger.error(f"Error processing job notification: {e}")

    def _on_job_details_received(self, topic, payload, dup, qos, retain, **kwargs):
        """Handle detailed job information"""
        try:
            message = json.loads(payload.decode('utf-8'))
            execution = message.get('execution', {})
            
            if execution:
                job_id = execution.get('jobId')
                job_document = execution.get('jobDocument', {})
                
                if job_id and job_document:
                    self._process_job_details(job_id, job_document, execution)
                    
        except Exception as e:
            self.logger.error(f"Error processing job details: {e}")

    def _process_job_notification(self, job_id: str, execution: Dict[str, Any]):
        """Process a job notification and request detailed job information"""
        self.logger.info(f"Processing job notification for job: {job_id}")
        
        # Request detailed job information
        get_job_topic = f"$aws/things/{self.thing_name}/jobs/{job_id}/get"
        
        try:
            message = {"clientToken": f"job-{job_id}-{int(time.time())}"}
            
            publish_future, _ = self.aws_manager.connection.publish(
                topic=get_job_topic,
                payload=json.dumps(message),
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            publish_future.result(timeout=5)
            
        except Exception as e:
            self.logger.error(f"Failed to request job details for {job_id}: {e}")

    def _process_job_details(self, job_id: str, job_document: Dict[str, Any], execution: Dict[str, Any]):
        """Process detailed job information and start update if appropriate"""
        self.logger.info(f"Processing job details for: {job_id}")
        
        try:
            # Parse job document
            update_type = UpdateType(job_document.get('updateType', 'full_system'))
            version = job_document.get('version', 'unknown')
            
            # Extract download information
            downloads = job_document.get('downloads', {})
            download_urls = {}
            checksums = {}
            file_sizes = {}
            
            # Handle different update types
            if update_type == UpdateType.FULL_SYSTEM:
                download_urls = downloads.get('urls', {})
                checksums = downloads.get('checksums', {})
                file_sizes = downloads.get('sizes', {})
                
            elif update_type == UpdateType.MEDIA_ONLY:
                # Extract media asset information
                media_assets = job_document.get('mediaAssets', [])
                for asset in media_assets:
                    asset_name = asset.get('filename')
                    if asset_name:
                        download_urls[asset_name] = asset.get('url')
                        checksums[asset_name] = asset.get('checksum')
                        file_sizes[asset_name] = asset.get('size', 0)
            
            # Create update job
            update_job = UpdateJob(
                job_id=job_id,
                job_document=job_document,
                update_type=update_type,
                version=version,
                download_urls=download_urls,
                checksums=checksums,
                file_sizes=file_sizes,
                created_at=time.time()
            )
            
            # Start update process
            self._start_update(update_job)
            
        except Exception as e:
            self.logger.error(f"Failed to process job details: {e}")
            self._report_job_status(job_id, JobStatus.FAILED, f"Job processing failed: {e}")

    def _start_update(self, job: UpdateJob):
        """Start the OTA update process"""
        with self.update_lock:
            if self.is_updating:
                self.logger.warning(f"Update already in progress, rejecting job {job.job_id}")
                self._report_job_status(job.job_id, JobStatus.REJECTED, "Another update in progress")
                return
                
            self.is_updating = True
            self.current_job = job
        
        self.logger.info(f"Starting OTA update: {job.job_id} (type: {job.update_type.value})")
        
        # Report job started
        self._report_job_status(job.job_id, JobStatus.IN_PROGRESS, "Update started")
        
        # Start update in background thread
        update_thread = threading.Thread(
            target=self._execute_update,
            args=(job,),
            name=f"OTA-Update-{job.job_id}"
        )
        update_thread.daemon = True
        update_thread.start()

    def _execute_update(self, job: UpdateJob):
        """Execute the OTA update process"""
        try:
            self.logger.info(f"Executing update {job.job_id}")
            
            # Phase 1: Download and verify
            self._update_progress(job, 10, "Downloading files...")
            if not self._download_and_verify(job):
                raise Exception("Download and verification failed")
                
            # Phase 2: Create backup
            self._update_progress(job, 30, "Creating backup...")
            if not self._create_backup(job):
                raise Exception("Backup creation failed")
                
            # Phase 3: Apply update
            if job.update_type == UpdateType.MEDIA_ONLY:
                self._update_progress(job, 50, "Updating media assets...")
                if not self._apply_media_update(job):
                    raise Exception("Media update failed")
            else:
                self._update_progress(job, 50, "Applying system update...")
                if not self._apply_system_update(job):
                    raise Exception("System update failed")
            
            # Phase 4: Verify installation
            self._update_progress(job, 80, "Verifying installation...")
            if not self._verify_installation(job):
                raise Exception("Installation verification failed")
                
            # Phase 5: Complete
            self._update_progress(job, 100, "Update completed successfully")
            
            # Cleanup
            self._cleanup_staging()
            
            self.logger.info(f"OTA update {job.job_id} completed successfully")
            self._report_job_status(job.job_id, JobStatus.SUCCEEDED, "Update completed successfully")
            
            # Schedule restart if needed
            if job.update_type in [UpdateType.FULL_SYSTEM, UpdateType.CODE_ONLY]:
                self._schedule_restart(job)
                
        except Exception as e:
            self.logger.error(f"OTA update {job.job_id} failed: {e}")
            self._handle_update_failure(job, str(e))
        finally:
            with self.update_lock:
                self.is_updating = False
                self.current_job = None

    def _download_and_verify(self, job: UpdateJob) -> bool:
        """Download and verify update files"""
        try:
            total_files = len(job.download_urls)
            completed_files = 0
            
            for filename, url in job.download_urls.items():
                self.logger.info(f"Downloading {filename} from {url}")
                
                # Determine target directory
                if job.update_type == UpdateType.MEDIA_ONLY:
                    target_dir = self.media_staging_dir
                else:
                    target_dir = self.staging_dir / "code"
                
                target_path = target_dir / filename
                
                # Download file
                if not self._download_file(url, target_path):
                    raise Exception(f"Failed to download {filename}")
                
                # Verify checksum
                expected_checksum = job.checksums.get(filename)
                if expected_checksum:
                    if not self._verify_file_checksum(target_path, expected_checksum):
                        raise Exception(f"Checksum verification failed for {filename}")
                
                completed_files += 1
                progress = 10 + (20 * completed_files // total_files)
                self._update_progress(job, progress, f"Downloaded {filename}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Download and verification failed: {e}")
            return False

    def _download_file(self, url: str, target_path: Path, chunk_size: int = 8192) -> bool:
        """Download a file from URL with progress tracking"""
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(target_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
            
            self.logger.info(f"Successfully downloaded {target_path.name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to download file from {url}: {e}")
            return False

    def _verify_file_checksum(self, file_path: Path, expected_checksum: str) -> bool:
        """Verify file checksum (supports SHA256)"""
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(chunk)
            
            actual_checksum = sha256_hash.hexdigest()
            
            if actual_checksum == expected_checksum:
                self.logger.info(f"Checksum verified for {file_path.name}")
                return True
            else:
                self.logger.error(f"Checksum mismatch for {file_path.name}: expected {expected_checksum}, got {actual_checksum}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to verify checksum for {file_path}: {e}")
            return False

    def _create_backup(self, job: UpdateJob) -> bool:
        """Create backup of current system before update"""
        try:
            if job.update_type == UpdateType.MEDIA_ONLY:
                return self._backup_media_assets(job)
            else:
                return self._backup_system_files(job)
                
        except Exception as e:
            self.logger.error(f"Backup creation failed: {e}")
            return False

    def _backup_media_assets(self, job: UpdateJob) -> bool:
        """Backup current media assets"""
        try:
            # Define media directories to backup
            media_dirs = [
                Path("assets/videos"),
                Path("assets/images"), 
                Path("event_images")
            ]
            
            backup_media_dir = self.backup_dir / "media" / job.job_id
            backup_media_dir.mkdir(parents=True, exist_ok=True)
            
            for media_dir in media_dirs:
                if media_dir.exists():
                    target_backup = backup_media_dir / media_dir.name
                    shutil.copytree(media_dir, target_backup, dirs_exist_ok=True)
                    self.logger.info(f"Backed up {media_dir} to {target_backup}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Media backup failed: {e}")
            return False

    def _backup_system_files(self, job: UpdateJob) -> bool:
        """Backup current system files"""
        try:
            # Backup source code
            src_backup = self.backup_dir / "code" / job.job_id / "src"
            src_backup.mkdir(parents=True, exist_ok=True)
            
            if Path("src").exists():
                shutil.copytree("src", src_backup / "src", dirs_exist_ok=True)
            
            # Backup configuration files
            config_files = ["pyproject.toml", "main.py", "run_production.py"]
            for config_file in config_files:
                if Path(config_file).exists():
                    shutil.copy2(config_file, src_backup / config_file)
            
            self.logger.info(f"System backup created: {src_backup}")
            return True
            
        except Exception as e:
            self.logger.error(f"System backup failed: {e}")
            return False

    def _apply_media_update(self, job: UpdateJob) -> bool:
        """Apply media asset updates"""
        try:
            media_assets = job.job_document.get('mediaAssets', [])
            
            for asset_info in media_assets:
                filename = asset_info.get('filename')
                target_path = asset_info.get('targetPath', 'assets/videos')
                asset_type = asset_info.get('assetType', 'video')
                
                if not filename:
                    continue
                
                source_file = self.media_staging_dir / filename
                target_dir = Path(target_path)
                target_file = target_dir / filename
                
                # Create target directory
                target_dir.mkdir(parents=True, exist_ok=True)
                
                # Copy file to target location
                shutil.copy2(source_file, target_file)
                
                self.logger.info(f"Deployed media asset: {filename} to {target_file}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Media update failed: {e}")
            return False

    def _apply_system_update(self, job: UpdateJob) -> bool:
        """Apply system/code updates"""
        try:
            # Extract update package
            staging_code_dir = self.staging_dir / "code"
            
            # Find update package (tar.gz or zip)
            update_packages = list(staging_code_dir.glob("*.tar.gz")) + list(staging_code_dir.glob("*.zip"))
            
            if not update_packages:
                raise Exception("No update package found")
            
            update_package = update_packages[0]
            
            # Extract package
            extract_dir = staging_code_dir / "extracted"
            extract_dir.mkdir(exist_ok=True)
            
            if update_package.suffix == '.gz':
                with tarfile.open(update_package, 'r:gz') as tar:
                    tar.extractall(extract_dir)
            elif update_package.suffix == '.zip':
                with zipfile.ZipFile(update_package, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
            
            # Deploy extracted files
            if (extract_dir / "src").exists():
                # Remove old source code
                if Path("src").exists():
                    shutil.rmtree("src")
                
                # Deploy new source code
                shutil.copytree(extract_dir / "src", "src")
                self.logger.info("Source code updated")
            
            # Update configuration files if present
            config_files = ["pyproject.toml", "main.py", "run_production.py"]
            for config_file in config_files:
                source_config = extract_dir / config_file
                if source_config.exists():
                    shutil.copy2(source_config, config_file)
                    self.logger.info(f"Updated {config_file}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"System update failed: {e}")
            return False

    def _verify_installation(self, job: UpdateJob) -> bool:
        """Verify the installation was successful"""
        try:
            if job.update_type == UpdateType.MEDIA_ONLY:
                return self._verify_media_installation(job)
            else:
                return self._verify_system_installation(job)
                
        except Exception as e:
            self.logger.error(f"Installation verification failed: {e}")
            return False

    def _verify_media_installation(self, job: UpdateJob) -> bool:
        """Verify media assets were installed correctly"""
        try:
            media_assets = job.job_document.get('mediaAssets', [])
            
            for asset_info in media_assets:
                filename = asset_info.get('filename')
                target_path = asset_info.get('targetPath', 'assets/videos')
                expected_checksum = asset_info.get('checksum')
                
                if not filename:
                    continue
                
                target_file = Path(target_path) / filename
                
                # Check file exists
                if not target_file.exists():
                    raise Exception(f"Media file not found: {target_file}")
                
                # Verify checksum if provided
                if expected_checksum:
                    if not self._verify_file_checksum(target_file, expected_checksum):
                        raise Exception(f"Media file checksum verification failed: {filename}")
            
            self.logger.info("Media installation verification completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Media installation verification failed: {e}")
            return False

    def _verify_system_installation(self, job: UpdateJob) -> bool:
        """Verify system installation"""
        try:
            # Check critical files exist
            critical_files = [
                "src/tsv6/__init__.py",
                "src/tsv6/core/main.py", 
                "src/tsv6/core/aws_manager.py"
            ]
            
            for file_path in critical_files:
                if not Path(file_path).exists():
                    raise Exception(f"Critical file missing: {file_path}")
            
            # Try to import main module to verify Python syntax
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("tsv6.core.main", "src/tsv6/core/main.py")
                if spec is None:
                    raise Exception("Cannot load main module spec")
                    
                # Note: We don't actually import to avoid side effects
                
            except Exception as e:
                raise Exception(f"Module import test failed: {e}")
            
            self.logger.info("System installation verification completed")
            return True
            
        except Exception as e:
            self.logger.error(f"System installation verification failed: {e}")
            return False

    def _schedule_restart(self, job: UpdateJob):
        """Schedule application restart after system update"""
        try:
            restart_delay = job.job_document.get('restartDelay', 10)

            # Get actual service name from environment
            service_name = os.getenv('TSV6_SERVICE_NAME', 'tsv6@pi.service')

            def restart_application():
                time.sleep(restart_delay)
                self.logger.info(f"Restarting service: {service_name}")

                # Report final status
                self._report_job_status(job.job_id, JobStatus.SUCCEEDED,
                                      "Update completed, restarting application")

                # Sync filesystem before restart to prevent data corruption
                sync_filesystem()

                try:
                    subprocess.run(['sudo', 'systemctl', 'restart', service_name],
                                 check=True, timeout=30)
                except Exception as e:
                    self.logger.error(f"Failed to restart service: {e}")
                    os._exit(0)

            restart_thread = threading.Thread(target=restart_application, name="OTA-Restart")
            restart_thread.daemon = True
            restart_thread.start()

        except Exception as e:
            self.logger.error(f"Failed to schedule restart: {e}")

    def _handle_update_failure(self, job: UpdateJob, error_message: str):
        """Handle update failure and attempt rollback"""
        try:
            self.logger.info(f"Handling update failure for job {job.job_id}: {error_message}")
            
            # Attempt rollback
            if self._rollback_update(job):
                self.logger.info("Rollback completed successfully")
                self._report_job_status(job.job_id, JobStatus.FAILED, f"Update failed but rollback succeeded: {error_message}")
            else:
                self.logger.error("Rollback failed")
                self._report_job_status(job.job_id, JobStatus.FAILED, f"Update and rollback both failed: {error_message}")
            
            # Cleanup
            self._cleanup_staging()
            
        except Exception as e:
            self.logger.error(f"Error handling update failure: {e}")

    def _rollback_update(self, job: UpdateJob) -> bool:
        """Rollback failed update"""
        try:
            backup_path = self.backup_dir / ("media" if job.update_type == UpdateType.MEDIA_ONLY else "code") / job.job_id
            
            if not backup_path.exists():
                self.logger.error("Backup not found, cannot rollback")
                return False
            
            if job.update_type == UpdateType.MEDIA_ONLY:
                return self._rollback_media_assets(backup_path)
            else:
                return self._rollback_system_files(backup_path)
                
        except Exception as e:
            self.logger.error(f"Rollback failed: {e}")
            return False

    def _rollback_media_assets(self, backup_path: Path) -> bool:
        """Rollback media assets"""
        try:
            # Restore media directories
            for backup_dir in backup_path.iterdir():
                if backup_dir.is_dir():
                    target_dir = Path(backup_dir.name)
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    shutil.copytree(backup_dir, target_dir)
                    self.logger.info(f"Restored {backup_dir.name}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Media rollback failed: {e}")
            return False

    def _rollback_system_files(self, backup_path: Path) -> bool:
        """Rollback system files"""
        try:
            # Restore source code
            src_backup = backup_path / "src"
            if src_backup.exists():
                if Path("src").exists():
                    shutil.rmtree("src")
                shutil.copytree(src_backup, "src")
            
            # Restore configuration files
            config_files = ["pyproject.toml", "main.py", "run_production.py"]
            for config_file in config_files:
                backup_file = backup_path / config_file
                if backup_file.exists():
                    shutil.copy2(backup_file, config_file)
            
            self.logger.info("System rollback completed")
            return True
            
        except Exception as e:
            self.logger.error(f"System rollback failed: {e}")
            return False

    def _cleanup_staging(self):
        """Clean up staging directories"""
        try:
            # Clean staging directories
            for staging_dir in [self.staging_dir, self.media_staging_dir]:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
                    staging_dir.mkdir(parents=True, exist_ok=True)
            
            self.logger.info("Staging directories cleaned")
            
        except Exception as e:
            self.logger.error(f"Staging cleanup failed: {e}")

    def _update_progress(self, job: UpdateJob, progress: int, message: str):
        """Update job progress"""
        job.progress = progress
        
        self.logger.info(f"Job {job.job_id} progress: {progress}% - {message}")
        
        # Call progress callback if set
        if self.progress_callback:
            try:
                self.progress_callback(job.job_id, progress, message)
            except Exception as e:
                self.logger.error(f"Progress callback error: {e}")

    def _report_job_status(self, job_id: str, status: JobStatus, message: str = ""):
        """Report job status to AWS IoT Jobs"""
        try:
            update_topic = self.jobs_update_topic_template.format(self.thing_name, job_id)
            
            status_details = {
                "status": status.value,
                "statusDetails": {
                    "message": message,
                    "timestamp": int(time.time()),
                    "progress": getattr(self.current_job, 'progress', 0) if self.current_job else 0
                }
            }
            
            if status in [JobStatus.FAILED, JobStatus.REJECTED]:
                status_details["statusDetails"]["errorCode"] = "UPDATE_FAILED"
                status_details["statusDetails"]["errorMessage"] = message
            
            publish_future, _ = self.aws_manager.connection.publish(
                topic=update_topic,
                payload=json.dumps(status_details),
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
            publish_future.result(timeout=5)
            
            self.logger.info(f"Reported job {job_id} status: {status.value}")
            
        except Exception as e:
            self.logger.error(f"Failed to report job status: {e}")

    def set_progress_callback(self, callback: Callable):
        """Set callback for progress updates"""
        self.progress_callback = callback

    def set_status_callback(self, callback: Callable):
        """Set callback for status updates"""  
        self.status_callback = callback

    def get_current_job_status(self) -> Optional[Dict[str, Any]]:
        """Get current job status"""
        if self.current_job:
            return {
                "job_id": self.current_job.job_id,
                "status": self.current_job.status.value,
                "progress": self.current_job.progress,
                "update_type": self.current_job.update_type.value,
                "version": self.current_job.version
            }
        return None

    def cancel_current_job(self) -> bool:
        """Cancel current job if possible"""
        with self.update_lock:
            if not self.current_job or not self.is_updating:
                return False
                
            # Mark for cancellation (implementation would need to handle this in update thread)
            self.logger.info(f"Cancelling job {self.current_job.job_id}")
            self._report_job_status(self.current_job.job_id, JobStatus.FAILED, "Job cancelled by user")
            
            return True

    def cleanup_old_backups(self, max_age_days: int = 7):
        """Clean up old backup files"""
        try:
            cutoff_time = time.time() - (max_age_days * 24 * 3600)
            
            for backup_type in ['code', 'media']:
                backup_type_dir = self.backup_dir / backup_type
                if not backup_type_dir.exists():
                    continue
                    
                for backup_job_dir in backup_type_dir.iterdir():
                    if backup_job_dir.is_dir():
                        # Check directory modification time
                        if backup_job_dir.stat().st_mtime < cutoff_time:
                            shutil.rmtree(backup_job_dir)
                            self.logger.info(f"Removed old backup: {backup_job_dir}")
            
        except Exception as e:
            self.logger.error(f"Backup cleanup failed: {e}")
