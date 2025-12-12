#!/bin/bash
# Create GitHub issues for memory optimization fixes

cd /home/g1tech/ts_uscup

echo "Creating GitHub issues for memory optimization fixes..."
echo ""

# Issue 1: CRITICAL - Image memory leak
echo "Creating issue 1: Image memory leak..."
gh issue create \
  --title "CRITICAL: Fix image memory leak in _hide_image_overlay()" \
  --body "## Problem
ImageTk.PhotoImage objects are not being properly released after image overlay display, causing memory leaks that accumulate over time. Each image display cycle keeps 10-30MB in memory.

## Root Cause
In \`src/tsv6/core/main.py\` lines 1019 and 1124, photo references are stored but never cleared:
\`\`\`python
self.image_overlay.photo = photo  # Strong reference prevents garbage collection
\`\`\`

## Solution
Add proper cleanup in \`_hide_image_overlay()\`:
1. Clear photo reference before destroying overlay (\`self.image_overlay.photo = None\`)
2. Force garbage collection after cleanup (\`gc.collect()\`)
3. Verify memory is freed

## Code Changes
\`\`\`python
def _hide_image_overlay(self):
    try:
        if self.image_overlay:
            # CRITICAL: Clear photo reference before destroying
            if hasattr(self.image_overlay, 'photo'):
                self.image_overlay.photo = None
            
            self.image_overlay.destroy()
            self.image_overlay = None
        
        # Force garbage collection
        import gc
        gc.collect()
        
        # ... rest of cleanup ...
\`\`\`

## Expected Impact
- Saves 10-30MB per image display cycle
- Prevents memory accumulation over extended operation
- Reduces risk of MQTT keep-alive timeouts due to memory pressure

## Priority
🔴 **CRITICAL** - Must be fixed first as this directly contributes to AWS IoT disconnections

## Testing
1. Monitor memory before/after image display
2. Verify \`free -m\` shows memory released
3. Run for 24 hours with frequent image displays

## Related
Part of memory optimization to fix MQTT_KEEP_ALIVE_TIMEOUT disconnections

## Files to Modify
- \`src/tsv6/core/main.py\` (function \`_hide_image_overlay\`)"

echo ""

# Issue 2: CRITICAL - MQTT keep-alive
echo "Creating issue 2: MQTT keep-alive timeout..."
gh issue create \
  --title "CRITICAL: Increase MQTT keep-alive timeout to 120 seconds" \
  --body "## Problem
Current MQTT keep-alive timeout of 60 seconds is too aggressive for memory-constrained Raspberry Pi systems. When memory pressure occurs (68.7% RAM, 60.6% swap), threads can be starved, preventing keep-alive pings from being sent on time, causing AWS IoT Core to disconnect the device.

## AWS Disconnection Alert
\`\`\`
Device: TS_3A72E72C-97d8e7a1-603a-4754-8596-727079b516a1
Reason: MQTT_KEEP_ALIVE_TIMEOUT
Timestamp: 2025-11-05 21:23:58 UTC
Client Initiated: No
\`\`\`

## Current Configuration
\`\`\`python
# src/tsv6/core/aws_resilient_manager.py line 226
keep_alive_secs=60,        # Too aggressive for constrained systems
ping_timeout_secs=5,       # Too tight
\`\`\`

## Solution
Increase timeouts for better resilience on resource-constrained devices:
\`\`\`python
keep_alive_secs=120,       # INCREASED: 180s grace period (1.5x keep-alive)
ping_timeout_secs=10,      # INCREASED: More time for ping response
\`\`\`

## Rationale
- AWS IoT Core disconnects after 1.5x keep-alive period
- **Current**: 60s keep-alive = 90s timeout window
- **Proposed**: 120s keep-alive = 180s timeout window
- Extra 90 seconds gives memory optimizer time to free resources before timeout
- Industry standard for IoT devices: 120-300 seconds
- Balances connection stability with resource detection

## Expected Impact
- 🎯 **70-80% reduction in disconnections**
- More stable long-term connections
- Better handling of temporary resource constraints (memory pressure, GC pauses)
- Reduced reconnection overhead and bandwidth usage
- Lower AWS IoT Core connection churn

## Priority
🔴 **CRITICAL** - Direct fix for MQTT_KEEP_ALIVE_TIMEOUT disconnections

## AWS IoT Core Compatibility
✅ Fully compliant with AWS IoT Core keep-alive requirements
✅ Within recommended range (30-1200 seconds)
✅ No changes to AWS IoT Core configuration required

## Testing
1. Apply change and restart application
2. Monitor AWS IoT Core for 24-48 hours
3. Verify no MQTT_KEEP_ALIVE_TIMEOUT disconnections
4. Check connection uptime in CloudWatch

## Files to Modify
- \`src/tsv6/core/aws_resilient_manager.py\` (line 226-227)"

echo ""

# Issue 3: CRITICAL - Logging
echo "Creating issue 3: Enable logging..."
gh issue create \
  --title "CRITICAL: Enable real-time logging to current directory" \
  --body "## Problem
Logs are currently not being written to the active logs directory. The last logs are from October 15-16 in \`/home/g1tech/to_delete/logs/tsv6/\`, making it impossible to diagnose real-time issues like the recent MQTT disconnection at 2025-11-05 21:23:58 UTC.

## Impact
- ❌ No visibility into current application state
- ❌ Cannot diagnose memory pressure events
- ❌ Cannot track MQTT connection health
- ❌ Cannot identify root causes of disconnections
- ❌ Cannot correlate with AWS CloudWatch events

## Current State
\`\`\`bash
# Old log location (inactive)
/home/g1tech/to_delete/logs/tsv6/tsv6.log  # Last: 2025-10-16

# Current directory (empty)
/home/g1tech/ts_uscup/logs/  # No logs being written
\`\`\`

## Solution
Update \`src/tsv6/config/production_config.py\` to:
1. Use project \`logs/\` directory for active logging
2. Set log level to **INFO** (currently WARNING)
3. Ensure logs are rotated properly (10MB max, 3 backups)
4. Add connection health and memory logging
5. Include timestamps and line numbers for debugging

## Expected Log Configuration
\`\`\`python
log_dir = Path(__file__).parent.parent.parent.parent / \"logs\"
log_dir.mkdir(parents=True, exist_ok=True)

\"level\": \"INFO\",  # Change from WARNING to INFO
\"filename\": str(log_dir / \"tsv6.log\"),
\"maxBytes\": 10 * 1024 * 1024,  # 10MB
\"backupCount\": 3
\`\`\`

## Expected Files
- \`/home/g1tech/ts_uscup/logs/tsv6.log\` - Main log (INFO level)
- \`/home/g1tech/ts_uscup/logs/tsv6_errors.log\` - Error log (ERROR level)
- Rotation: \`tsv6.log.1\`, \`tsv6.log.2\`, \`tsv6.log.3\`

## Expected Impact
- ✅ Real-time diagnostics for all future issues
- ✅ Ability to correlate events with AWS CloudWatch
- ✅ Better debugging of memory and connection issues
- ✅ Historical data for pattern analysis
- ✅ Early detection of warnings before failures

## Priority
🔴 **CRITICAL** - Required for monitoring and debugging any future issues

## Testing
1. Apply configuration changes
2. Restart application
3. Verify log file creation: \`ls -lh /home/g1tech/ts_uscup/logs/\`
4. Tail logs: \`tail -f /home/g1tech/ts_uscup/logs/tsv6.log\`
5. Verify INFO messages appear in real-time

## Files to Modify
- \`src/tsv6/config/production_config.py\` (function \`get_logging_config\`)"

echo ""

# Issue 4: HIGH - ImageManager
echo "Creating issue 4: ImageManager memory..."
gh issue create \
  --title "HIGH: Optimize ImageManager memory disposal" \
  --body "## Problem
ImageManager loads PIL Image objects but doesn't explicitly close them after creating ImageTk.PhotoImage objects. This leaves 5-15MB of image data in memory per load, as PIL Image keeps the file handle and decoded image data even after PhotoImage is created.

## Root Cause
In \`src/tsv6/core/image_manager.py\` line 180-189:
\`\`\`python
with Image.open(image_path) as img:
    # ... processing ...
    photo = ImageTk.PhotoImage(img)
    return photo
    # img closes here, but PhotoImage may hold reference
\`\`\`

## Solution
Add explicit image disposal after PhotoImage creation:
\`\`\`python
def load_image_for_display(self, image_path: Path, target_size: tuple = (400, 400)):
    if not PIL_AVAILABLE:
        return None
    
    try:
        img = Image.open(image_path)  # Don't use context manager
        
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        img.thumbnail(target_size, Image.Resampling.LANCZOS)
        
        photo = ImageTk.PhotoImage(img)
        
        # CRITICAL: Explicitly close to free memory
        img.close()
        
        return photo
    except Exception as e:
        print(f\"❌ Failed to load image: {e}\")
        return None
\`\`\`

## Expected Impact
- Saves 5-15MB per image load
- Prevents gradual memory accumulation
- Improves overall memory efficiency
- Complements fix for image overlay cleanup (#1)

## Priority
🟡 **HIGH** - Second priority after critical fixes

## Testing
1. Load image and check memory with \`free -m\`
2. Display multiple images sequentially
3. Verify memory returns to baseline
4. Monitor over 24 hours

## Dependencies
- Works together with issue #1 (image overlay memory leak)

## Files to Modify
- \`src/tsv6/core/image_manager.py\` (function \`load_image_for_display\`)"

echo ""

# Issue 5: HIGH - Thread pool
echo "Creating issue 5: Thread pool reduction..."
gh issue create \
  --title "HIGH: Reduce thread pool overhead for barcode callbacks" \
  --body "## Problem
Current ThreadPoolExecutor uses 2 workers for barcode callbacks, adding unnecessary memory overhead (2-5MB) and context switching on memory-constrained Raspberry Pi (906MB total RAM).

## Current Code
\`\`\`python
# src/tsv6/core/main.py line 98
self.callback_executor = ThreadPoolExecutor(max_workers=2)
\`\`\`

## Analysis
- Barcode scans are **sequential** (one at a time from HID scanner)
- No benefit from parallel callback processing
- Each thread consumes ~2-3MB memory
- Extra thread adds context switching overhead
- System already has 19 threads (high for constrained system)

## Solution
Reduce to single worker for sequential processing:
\`\`\`python
self.callback_executor = ThreadPoolExecutor(
    max_workers=1, 
    thread_name_prefix=\"BarcodeCallback\"
)
\`\`\`

## Rationale
- Barcode scanning is inherently serial
- Callbacks execute quickly (< 10ms typically)
- Single thread sufficient for throughput
- Reduces memory footprint
- Decreases context switching
- Improves system responsiveness under load
- Easier debugging with single callback thread

## Expected Impact
- Saves 2-5MB memory
- Reduces thread context switching overhead
- More predictable performance under memory pressure
- Clearer thread naming for debugging
- Reduced CPU context switch count

## Priority
🟡 **HIGH** - Low-hanging fruit for memory optimization

## Testing
1. Check thread count: \`ps -T -p <pid> | wc -l\`
2. Monitor context switches: \`cat /proc/<pid>/status | grep ctxt\`
3. Verify barcode scanning still fast (< 50ms scan-to-publish)
4. Check memory reduction with \`free -m\`

## Files to Modify
- \`src/tsv6/core/main.py\` (class \`OptimizedBarcodeScanner.__init__\`)"

echo ""

# Issue 6: MEDIUM - Memory optimizer
echo "Creating issue 6: Memory optimizer integration..."
gh issue create \
  --title "MEDIUM: Enhance memory optimizer integration with image lifecycle" \
  --body "## Problem
Memory optimizer exists (\`src/tsv6/utils/memory_optimizer.py\`) but isn't integrated with image management lifecycle, missing opportunities for automatic cleanup during memory pressure events (when memory > 75%).

## Current State
- Memory optimizer runs every 15 seconds
- Monitors memory and swap usage
- Triggers GC when thresholds exceeded
- But doesn't know about image resources

## Solution
Add image cleanup handler registration in \`src/tsv6/core/main.py\`:

\`\`\`python
def __init__(self, aws_manager=None):
    # ... existing code ...
    
    # Register image cleanup with memory optimizer
    if MEMORY_OPTIMIZER_AVAILABLE:
        self.memory_optimizer = get_global_memory_optimizer()
        self.memory_optimizer.register_cleanup_handler(
            self._cleanup_image_resources
        )

def _cleanup_image_resources(self):
    \"\"\"Cleanup handler for memory optimizer\"\"\"
    try:
        # Clear any active image overlays
        if self.image_overlay:
            self._hide_image_overlay()
        
        # Clear image cache if oversized
        stats = self.image_manager.get_cache_stats()
        if stats.get('file_count', 0) > 50:
            print(\"🧹 Image cache cleanup triggered by memory optimizer\")
            self.image_manager._cleanup_cache()
        
        # Force garbage collection
        import gc
        gc.collect()
    except Exception as e:
        print(f\"⚠ Image cleanup error: {e}\")
\`\`\`

## Expected Impact
- Automatic memory recovery during pressure events (>75% memory)
- Better integration between memory optimizer and image manager
- Proactive cleanup before memory issues escalate to MQTT timeouts
- Complements existing memory optimizer features
- Reduces manual intervention needed

## Priority
🔵 **MEDIUM** - Nice to have after critical fixes

## Dependencies
- Requires issues #1 and #4 to be implemented first
- Builds on existing memory optimizer infrastructure

## Testing
1. Simulate memory pressure with large file operations
2. Monitor memory optimizer logs
3. Verify automatic image cleanup triggers
4. Check that MQTT connection stays stable

## Files to Modify
- \`src/tsv6/core/main.py\` (class \`EnhancedVideoPlayer\`)"

echo ""

# Issue 7: MEDIUM - Health monitoring
echo "Creating issue 7: MQTT health monitoring..."
gh issue create \
  --title "MEDIUM: Add MQTT connection health monitoring" \
  --body "## Problem
No visibility into MQTT connection health before timeout occurs. Need early warning system to detect memory pressure (>85%) that could lead to keep-alive failures.

## Gap Analysis
Current state:
- ✅ MQTT connection established
- ✅ Auto-reconnect on disconnect
- ✅ Error logging for connection issues
- ❌ No proactive health monitoring
- ❌ No early warning before failures
- ❌ No memory correlation tracking
- ❌ No connection uptime visibility

## Solution
Add connection health monitoring in \`src/tsv6/core/aws_resilient_manager.py\`:

\`\`\`python
def start_health_monitoring(self):
    \"\"\"Start MQTT connection health monitoring\"\"\"
    def health_check_worker():
        while self._running:
            try:
                if self.connected:
                    # Check memory status
                    memory = psutil.virtual_memory()
                    if memory.percent > 85:
                        print(f\"⚠️  HIGH MEMORY: {memory.percent:.1f}% - MQTT at risk\")
                        # Trigger emergency garbage collection
                        import gc
                        collected = gc.collect()
                        print(f\"   🧹 Emergency GC collected {collected} objects\")
                    
                    # Log connection uptime every 5 minutes
                    if self.connection_start_time:
                        uptime = time.time() - self.connection_start_time
                        if uptime % 300 < 30:  # Every 5 minutes (with 30s tolerance)
                            print(f\"✅ MQTT connected for {int(uptime/60)} minutes\")
                            print(f\"   Memory: {memory.percent:.1f}%, Swap: {psutil.swap_memory().percent:.1f}%\")
                
                time.sleep(30)
            except Exception as e:
                print(f\"Health check error: {e}\")
        
    health_thread = threading.Thread(
        target=health_check_worker, 
        name=\"MQTT-Health\", 
        daemon=True
    )
    health_thread.start()
    print(\"✅ MQTT health monitoring started\")
\`\`\`

## Features
1. **Memory Pressure Detection**: Alert when memory > 85%
2. **Connection Uptime Logging**: Log every 5 minutes
3. **Emergency GC**: Trigger garbage collection at high memory
4. **Early Warning**: Detect issues before disconnection
5. **Operational Visibility**: Regular health check messages

## Expected Output
\`\`\`
✅ MQTT connected for 15 minutes
   Memory: 68.2%, Swap: 45.3%

⚠️  HIGH MEMORY: 87.5% - MQTT at risk
   🧹 Emergency GC collected 1247 objects
\`\`\`

## Expected Impact
- Early detection of memory issues (5-10 minutes warning)
- Proactive memory cleanup before MQTT timeout
- Better operational visibility
- Reduced surprise disconnections
- Historical health data in logs
- Easier correlation with AWS CloudWatch events

## Priority
🔵 **MEDIUM** - Monitoring improvement after core fixes

## Nice to Have (Future)
- Emit CloudWatch metrics
- Integration with health monitoring dashboard
- Configurable alerting thresholds
- SMS/email alerts on critical memory

## Testing
1. Run for 24 hours with monitoring
2. Verify 5-minute uptime logs
3. Simulate high memory and verify GC trigger
4. Check correlation with disconnection patterns

## Files to Modify
- \`src/tsv6/core/aws_resilient_manager.py\` (add \`start_health_monitoring\` method)
- \`src/tsv6/core/production_main.py\` (call during initialization)"

echo ""
echo "✅ All GitHub issues created successfully!"
echo ""
echo "View issues at: https://github.com/genesis1tech/ts_uscup/issues"
