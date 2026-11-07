# Backend work

Follow the project's existing service and data-access conventions. Validate inputs at system boundaries, enforce authorization where data is accessed, and keep credentials out of logs.

Define failure and retry behavior for external calls. When changing persisted data or public APIs, account for existing callers and test the affected success and failure paths.
