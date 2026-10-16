# Migration Tracking System

This document describes the new migration tracking system that stores all Salesforce migration attempts and results in the database, allowing users to review their migration history and track success/failure rates.

## Overview

The migration tracking system provides:
- **Database storage** of all migration attempts and results
- **Batch processing** for large datasets with configurable batch sizes
- **Background job processing** for handling migrations asynchronously
- **Comprehensive tracking** of successful, failed, and partial migrations
- **User-specific migration history** with detailed analytics

## Database Schema

### Migration Batches Table
Stores information about each migration batch:
- `id`: Unique batch identifier
- `user_id`: User who initiated the migration
- `batch_name`: Human-readable batch name
- `document_type`: Type of document being migrated
- `total_records`: Total number of records in the batch
- `successful_records`: Number of successfully migrated records
- `failed_records`: Number of failed migrations
- `status`: Current status (in_progress, completed, failed)
- `mapping_config`: Field mapping configuration used
- `created_at`: When the batch was created
- `completed_at`: When the batch completed (if finished)
- `error_summary`: Summary of errors (if any)

### Migration Records Table
Stores individual record migration details:
- `id`: Unique record identifier
- `batch_id`: Reference to the migration batch
- `user_id`: User who owns the record
- `record_index`: Position in the original data array
- `source_data`: Original record data before migration
- `mapped_data`: Data after field mapping (if applicable)
- `salesforce_results`: Results from Salesforce API calls
- `status`: Migration status (success, failed, partial_success)
- `error_message`: Detailed error message (if failed)
- `created_at`: When the record was created
- `processed_at`: When the record was processed

## API Endpoints

### Standard Migration
- `POST /migrate-to-salesforce` - Migrate data with tracking
- Returns batch_id and summary information

### Migration History
- `GET /migration-batches` - List user's migration batches
- `GET /migration-batches/{batch_id}` - Get batch details
- `GET /migration-batches/{batch_id}/records` - Get batch records
- `GET /migration-summary` - Get user's migration statistics

### Background Migration
- `POST /start-background-migration` - Start background migration job
- `GET /background-migration-status/{job_id}` - Get job status
- `GET /background-migration-jobs` - List user's background jobs

## Usage Examples

### Standard Migration with Tracking
```python
# The existing /migrate-to-salesforce endpoint now automatically tracks migrations
response = await client.post("/migrate-to-salesforce", json={
    "data": records,
    "mapping": field_mapping,
    "document_type": "Student_Records"
})

# Response now includes batch tracking information
{
    "status": "success",
    "batch_id": "uuid-here",
    "summary": {
        "total": 150,
        "successful": 145,
        "failed": 5
    },
    "results": [...],
    "instance_url": "..."
}
```

### Background Migration for Large Datasets
```python
# Start background migration for large datasets
response = await client.post("/start-background-migration", json={
    "data": large_dataset,  # Can be thousands of records
    "mapping": field_mapping,
    "document_type": "Student_Records",
    "batch_size": 100,      # Optional; computed dynamically when omitted
    "max_concurrent": 5     # Max 5 concurrent migrations
})

# Returns job ID for tracking
{
    "status": "success",
    "job_id": "uuid-here",
    "total_records": 5000,
    "batch_size": 100,
    "max_concurrent": 5
}
```

### Check Migration History
```python
# Get user's migration batches
batches = await client.get("/migration-batches?limit=10&offset=0")

# Get specific batch details
batch_details = await client.get(f"/migration-batches/{batch_id}")

# Get records from a specific batch
records = await client.get(f"/migration-batches/{batch_id}/records?limit=50")

# Get migration summary statistics
summary = await client.get("/migration-summary")
```

## Configuration Options

### Background Migration Parameters
- **batch_size**: Optional. When omitted, computed dynamically from total records and destination limits (~100 batches). When provided, must be between 1 and destination max (Salesforce: 10000, Google Sheets: 5000, SQL: 500).
- **max_concurrent**: Maximum concurrent record migrations (1-20)

### Recommended Settings
- **Small datasets (< 100 records)**: Use standard migration
- **Medium/large datasets**: Omit batch_size for automatic sizing, or specify explicitly. max_concurrent=3–5 typically.

## Migration Status Tracking

### Batch Statuses
- `in_progress`: Migration is currently running
- `completed`: Migration finished successfully
- `failed`: Migration encountered an error

### Record Statuses
- `pending`: Record created, waiting for processing
- `success`: Record migrated successfully
- `failed`: Record migration failed
- `partial_success`: Some objects succeeded, others failed

## Error Handling

The system captures and stores:
- **API errors** from Salesforce
- **Validation errors** from field mapping
- **System errors** from the migration process
- **Network timeouts** and connection issues

All errors are stored with:
- Detailed error messages
- Timestamps
- Record context
- Batch association

## Performance Considerations

### Database Performance
- Indexes on frequently queried fields
- Batch operations for large datasets
- Efficient pagination for record retrieval

### Migration Performance
- Configurable concurrency limits
- Batch processing to avoid memory issues
- Background processing for large datasets

## Monitoring and Maintenance

### Automatic Cleanup
- Background jobs are automatically cleaned up after 24 hours
- Completed migration records are retained indefinitely
- Failed records are kept for debugging purposes

### Health Checks
- Monitor active background jobs
- Track migration success rates
- Alert on high failure rates

## Security Features

- **User isolation**: Users can only access their own migration data
- **Authentication required**: All endpoints require valid user authentication
- **Input validation**: All parameters are validated before processing
- **Rate limiting**: Configurable limits on concurrent migrations

## Troubleshooting

### Common Issues

1. **Migration stuck in "in_progress"**
   - Check background job status
   - Verify Salesforce API connectivity
   - Review error logs

2. **High failure rates**
   - Check field mapping configuration
   - Verify Salesforce object permissions
   - Review data quality issues

3. **Slow performance**
   - Reduce batch size
   - Lower concurrency limits
   - Check network latency

### Debug Information
- All migration attempts are logged with timestamps
- Error messages include context and stack traces
- Batch and record statuses provide detailed progress information

## Future Enhancements

- **Email notifications** for completed migrations
- **Retry mechanisms** for failed records
- **Migration templates** for common use cases
- **Advanced analytics** and reporting
- **Webhook support** for external integrations
