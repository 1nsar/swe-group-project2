# Architecture Deviations Report (Member 1 – Backend, Authentication & Permissions)

This document describes the deviations between the Assignment 1 design and the final implementation for the backend, authentication, and permission system.

For each deviation, we explain:
- what changed
- why it changed
- whether it is an improvement or a compromise

## 1. In-Memory Storage Instead of Database

**Original Design (Assignment 1):**  
Use PostgreSQL database for users, documents, and permissions.

**Final Implementation:**  
Used Python dictionaries (in-memory storage).

**Reason:**  
Simplifies development and allowed by assignment.

**Impact:**  
Data is lost after server restart.

**Evaluation:**  
Compromise

## 2. Simplified Authentication (JWT instead of OAuth)

**Original Design:**  
OAuth 2.0 / OIDC authentication.

**Final Implementation:**  
JWT-based authentication with access and refresh tokens.

**Reason:**  
OAuth is complex; JWT is simpler for this assignment.

**Impact:**  
Easier implementation, but less production-ready.

**Evaluation:**  
Improvement (for assignment scope)

## 3. Defined JWT Payload Structure

**Original Design:**  
JWT structure not defined.

**Final Implementation:**  

{
  "sub": "user_id",
  "email": "user@example.com",
  "username": "display_name",
  "type": "access"
}

**Reason:**  
Supports frontend and backend integration.

**Impact:**  
Clear contract across system.

**Evaluation:**  
Improvement

## 4. OAuth2 Form Login (Swagger Integration)

**Original Design:**  
JSON login request.

**Final Implementation:**  
OAuth2PasswordRequestForm used.

**Reason:**  
Works with FastAPI Swagger authentication UI.

**Impact:**  
Better testing experience.

**Evaluation:**  
Improvement

## 5. Server-Side Permission Enforcement

**Original Design:**  
Database-driven permission system.

**Final Implementation:**  
Helper functions (require_read, require_edit, require_owner).

**Reason:**  
Simpler and still secure.

**Impact:**  
Not scalable but effective.

**Evaluation:**  
Improvement (with limitations)

## Summary

The final system prioritizes simplicity and functionality over scalability and production-level architecture.
