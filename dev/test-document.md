# Solution Architecture — Citizen Notification Service

**Author:** Jane Smith, Platform Architecture Team
**Date:** 2026-04-02
**Status:** Draft — for architecture review

---

## 1. Overview

The Citizen Notification Service (CNS) is a new shared platform capability that enables government agencies to send transactional notifications to citizens across multiple channels: email, SMS, and in-app push. It is designed as a central service consumed by agency-facing APIs.

---

## 2. Business Context

Multiple agencies currently maintain their own notification stacks, leading to duplicated infrastructure, inconsistent delivery behaviour, and fragmented audit trails. CNS consolidates this into a single managed service owned by the Platform Architecture Team.

---

## 3. Architecture

### 3.1 Integration Pattern

Agencies integrate with CNS via a synchronous REST API exposed through the agency-facing API Gateway. Each notification request is accepted, validated, and immediately enqueued to an internal Azure Service Bus topic. Delivery workers consume from the topic and dispatch to the appropriate channel provider (SendGrid for email, Twilio for SMS).

The API response to the calling agency confirms acceptance only — delivery status is returned asynchronously via a webhook callback registered per agency at onboarding.

### 3.2 Authentication and Authorisation

Agency systems authenticate to the CNS API using OAuth 2.0 client credentials flow (machine-to-machine). Tokens are issued by the platform's shared Azure Active Directory B2C tenant. Each agency is issued a distinct client ID scoped to its allowed notification types and rate limits.

JWT tokens are validated at the API Gateway layer before requests reach CNS internals. No agency credentials are stored within CNS itself.

### 3.3 Data Handling

Notification payloads (recipient contact details, message content) are treated as personal information under the Privacy Act 2020. Payloads are encrypted in transit (TLS 1.2+) and at rest within Service Bus using Azure-managed keys.

Notification records — including recipient identifier, timestamp, channel, and delivery status — are retained for 90 days in Azure Cosmos DB for audit and redelivery purposes, then purged. No message body content is retained after dispatch.

Citizen contact details sourced from agency systems of record are not stored by CNS beyond the in-flight duration of the request.

### 3.4 Infrastructure

All components are deployed to Azure New Zealand North region. No data leaves New Zealand. The service is deployed as Azure Container Apps with autoscaling configured to handle peak load.

There is no on-premises component.

### 3.5 API Design

The CNS public API follows RESTful conventions. It is versioned via URL path (`/v1/notifications`). Request and response bodies are JSON. The API is documented via OpenAPI 3.0 spec, published to the internal developer portal.

Error responses use standard HTTP status codes. Rate limiting is enforced at the API Gateway layer and communicated via `Retry-After` headers.

### 3.6 Secrets Management

All service credentials (SendGrid API key, Twilio auth token, Service Bus connection strings) are stored in Azure Key Vault. Container Apps retrieve secrets at startup via managed identity — no secrets are present in environment variables, configuration files, or container images.

---

## 4. Known Gaps and Decisions Deferred

- **End-to-end delivery receipts**: The webhook callback mechanism is designed but not yet implemented. Agencies currently have no programmatic way to confirm delivery beyond the acceptance acknowledgement.
- **SMS provider redundancy**: A single SMS provider (Twilio) is used. Failover to a secondary provider is not in scope for v1.
- **Citizen opt-out**: A self-service opt-out mechanism for citizens is not included in v1. Agencies are responsible for honouring opt-out preferences before calling CNS.
