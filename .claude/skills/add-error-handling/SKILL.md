---
name: add-error-handling
description: Add comprehensive error handling to code. Use this for implementing robust error handling, validation, and recovery mechanisms.
---

Implement comprehensive error handling for the current code to make it robust and resilient to failures while maintaining good user experience.

## Steps

1. **Error Detection**
   - Identify potential failure points and edge cases
   - Find unhandled exceptions and error conditions
   - Detect missing validation and boundary checks
   - Analyze async operations and network calls

2. **Error Handling Strategy**
   - Implement try-catch blocks where appropriate
   - Add input validation and sanitization
   - Create meaningful error messages and logging
   - Design graceful degradation for non-critical failures

3. **Recovery Mechanisms**
   - Implement retry logic for transient failures
   - Add fallback options for service unavailability
   - Create circuit breakers for external dependencies
   - Design proper error propagation and handling

4. **User Experience**
   - Provide clear error messages to users
   - Implement proper error status codes for APIs
   - Add loading states and error boundaries for UI
   - Include helpful suggestions for error resolution

NEVER add inline comments to code. If you see unnecessary inline comments, remove them as part of your cleanup.
