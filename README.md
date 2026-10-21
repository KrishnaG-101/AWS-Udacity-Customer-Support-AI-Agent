# AWS Bedrock AgentCore: Autonomous E-Commerce Customer Support Agent

[![AWS Cloud](https://img.shields.io/badge/AWS-Cloud-232F3E?style=for-the-badge&logo=amazon-aws&logoColor=white)](https://aws.amazon.com/)
[![Amazon Bedrock](https://img.shields.io/badge/Amazon_Bedrock-AgentCore_Runtime-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)](https://aws.amazon.com/bedrock/)
[![Model](https://img.shields.io/badge/Model-Amazon_Nova_Lite-4285F4?style=for-the-badge)](https://aws.amazon.com/bedrock/nova/)
[![SDK](https://img.shields.io/badge/SDK-Strands_Agents-00C7B7?style=for-the-badge)](https://strandsagents.com/)
[![Protocol](https://img.shields.io/badge/Protocol-Model_Context_Protocol_(MCP)-8A2BE2?style=for-the-badge)](https://modelcontextprotocol.io/)
[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE.txt)

An enterprise-grade, autonomous customer support agent engineered on the **Amazon Bedrock AgentCore Runtime** and the **Strands SDK**. The assistant serves as an intelligent conversational interface for an e-commerce platform, capable of handling multi-turn transactional workflows (order lookups, return processing), grounded semantic search with **Bedrock Knowledge Bases (RAG)**, cross-session customer memory with **AgentCore Memory**, sandboxed arithmetic computation via **AgentCore Code Interpreter**, and live web navigation with **AgentCore Browser**.

---

## Table of Contents

- [Executive Overview](#executive-overview)
- [System Architecture](#system-architecture)
- [Key Engineering Highlights](#key-engineering-highlights)
- [Visual Proof & Production Verification](#visual-proof--production-verification)
  - [1. MCP Gateway Integrations (Order Tracking & Returns)](#1-mcp-gateway-integrations-order-tracking--returns)
  - [2. Knowledge Grounding (RAG) & Sandboxed Computation](#2-knowledge-grounding-rag--sandboxed-computation)
  - [3. Cross-Session Long-Term Memory Recall](#3-cross-session-long-term-memory-recall)
  - [4. Live Web Browsing via Playwright CDP](#4-live-web-browsing-via-playwright-cdp)
- [Task 8: Written Reflection](#task-8-written-reflection)
- [Repository Structure](#repository-structure)
- [Setup & Deployment Guide](#setup--deployment-guide)
  - [Prerequisites](#prerequisites)
  - [Step 1: Lambda Functions Deployment](#step-1-lambda-functions-deployment)
  - [Step 2: API Gateway REST API](#step-2-api-gateway-rest-api)
  - [Step 3: AgentCore Gateway Configuration](#step-3-agentcore-gateway-configuration)
  - [Step 4: Knowledge Base Creation & Sync](#step-4-knowledge-base-creation--sync)
  - [Step 5: AgentCore Memory Resource](#step-5-agentcore-memory-resource)
  - [Step 6: Direct Code Deployment](#step-6-direct-code-deployment)
  - [Step 7: Verification Invocations](#step-7-verification-invocations)
  - [Step 8: Infrastructure Teardown & Cost Cleanup](#step-8-infrastructure-teardown--cost-cleanup)
- [License](#license)

---

## Executive Overview

Modern customer service operations require systems that seamlessly bridge conversational intelligence with backend business transactions. Traditional chatbots suffer from context loss, hallucinations on pricing or policy, and brittle integrations. 

This project implements an end-to-end architecture solving these enterprise challenges:
* **Decoupled Tool Execution:** Connects backend services through the **AgentCore Gateway** using the **Model Context Protocol (MCP)**, standardizing tool invocation for API Gateway endpoints and AWS Lambda functions without hardcoded SDK wrappers.
* **Factual Domain Grounding:** Retrieves verified policies and product data from an **Amazon Bedrock Knowledge Base** backed by OpenSearch Serverless, eliminating policy hallucinations.
* **Persistent Context Across Sessions:** Retains customer identity, preferences, and facts across disconnected sessions using **AgentCore Memory** strategy namespaces (`customer_facts` and `customer_preferences`).
* **Deterministic Arithmetic in Sandbox:** Delegates loyalty point redemptions and tier discounts to the **AgentCore Code Interpreter**, guaranteeing exact numerical calculations.
* **Live Web Automation:** Leverages **AgentCore Browser** to navigate live web pages and inspect current content via an isolated Playwright Chrome DevTools Protocol (CDP) session.

---

## System Architecture

```text
                               +----------------------------------+
                               |     Incoming Customer Prompt     |
                               +-----------------+----------------+
                                                 |
                                                 v
                               +----------------------------------+
                               |  BedrockAgentCoreApp Entrypoint  |
                               |    - Actor & Session ID parsing  |
                               |    - Playwright POSIX Shim       |
                               +-----------------+----------------+
                                                 |
                                                 v
                      +----------------------------------------------------+
                      |     MemoryHook: MessageAddedEvent Interceptor      |
                      |  - Queries strategy namespaces (facts/preferences) |
                      |  - Injects [Customer Context] into prompt          |
                      +--------------------------+-------------------------+
                                                 |
                                                 v
                      +----------------------------------------------------+
                      |              Strands Agent Orchestrator            |
                      |          Amazon Nova 2 Lite (BedrockModel)         |
                      +---+------------------+-----------------+-------+---+
                          |                  |                 |       |
         +----------------+                  |                 |       +----------------+
         |                                   |                 |                        |
         v                                   v                 v                        v
+------------------+                +------------------+ +---------------+   +-------------------+
| MCP Gateway      |                | Bedrock KB (RAG) | | Code Interp.  |   | AgentCore Browser |
| (Streamable HTTP)|                | Retrieve API     | | (Sandboxed)   |   | (Playwright CDP)  |
+--------+---------+                +--------+---------+ +-------+-------+   +---------+---------+
         |                                   |                   |                     |
    +----+----+                              v                   v                     v
    |         |                      +---------------+   +---------------+   +-------------------+
    v         v                      | Product Specs |   | Exact Loyalty |   | Live Web Scraping |
[API GW]  [Lambda]                   | Return Window |   | & Tier Math   |   | & Page Title      |
(Orders)  (Refunds)                  | Policy Claims |   | (No LLM Drift)|   | Extraction        |
```

### Architectural Decisions & Rationale:
1. **Model Context Protocol (MCP) Gateway Integration:** Decouples the agent from concrete backend implementations. REST APIs and serverless Lambdas are abstracted as MCP tools, providing standard schema validation and unified audit trails.
2. **Dynamic Runtime Playwright Shim:** AWS serverless container runtimes mount `/var/task` as read-only. Deploying from Windows hosts omits Unix POSIX execute bits (`0o755`), causing `node` to fail. Our dynamic bootstrap copies the binary to `/tmp/playwright-node`, executes `chmod 0o755`, and sets `PLAYWRIGHT_NODEJS_PATH`, enabling zero-packaging browser automation.
3. **Dual-Strategy Memory Architecture:** Splits long-term context into `customer_facts` (`cs_agent/{actorId}/facts`) and `customer_preferences` (`cs_agent/{actorId}/preferences`), ensuring semantic recall stays bounded, structured, and noise-free.

---

## Key Engineering Highlights

| Feature | Implementation Mechanism | Production Impact |
|---|---|---|
| **Order Tracking** | API Gateway REST API target via AgentCore Gateway | Securely proxies multi-resource lookups (`/orders/{id}`, `/customers/{id}/orders`) without exposing database internals. |
| **Refund Processing** | Direct AWS Lambda MCP target with strict input schema | Automatically processes and validates return eligibility, generating audit-ready refund IDs. |
| **Policy RAG Grounding** | Bedrock Agent Runtime `retrieve` API (`CustomerSupportKB`) | Delivers verifiable, cited return windows and tier perks directly from vector embeddings. |
| **Cross-Session Recall** | Strands `HookProvider` + AgentCore Memory Event API | Captures customer names and interaction styles in Session A; automatically recalls them in Session B. |
| **Deterministic Math** | AgentCore Code Interpreter (`executeCode` in Python sandbox) | Eliminates LLM floating-point calculation errors for discounts, points redemption, and order totals. |
| **Browser Tooling** | `AgentCoreBrowser` over remote AWS-managed CDP stream | Scrapes live web URLs in headless environments without local browser overhead or credential leakage. |

---

## Visual Proof & Production Verification

All capabilities have been validated in the live cloud environment using `agentcore invoke`.

### 1. MCP Gateway Integrations (Order Tracking & Returns)

The agent connects to the AgentCore Gateway to query shipping status from API Gateway and process returns via AWS Lambda:

| Test 1: Order Tracking (`ORD-001`) | Test 2: Refund Processing (`ORD-002`) |
|:---:|:---:|
| <img src="assets/Task%201%20Completion%20SS.png" alt="Test 1 Order Tracking" width="450"/> | <img src="assets/Task%202%20Completion%20SS.png" alt="Test 2 Refund Processing" width="450"/> |
| *Retrieved order status `SHIPPED`, carrier UPS, tracking number `TRK987654321`, delivery Oct 23.* | *Processed return for Kindle Paperwhite, generating refund ID `REF-0XXULXBU` with 3–5 day timeline.* |

```bash
# Test 1 — Order Tracking
uv run agentcore invoke '{"prompt": "Can you track order ORD-001?", "customer_id": "CUST-123", "session_id": "t1"}'

# Test 2 — Refund Processing
uv run agentcore invoke '{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.", "customer_id": "CUST-123", "session_id": "t2"}'
```

---

### 2. Knowledge Grounding (RAG) & Sandboxed Computation

The agent retrieves factual policy data from Bedrock Knowledge Bases and runs sandboxed Python code for exact loyalty point calculations:

| Test 3: Bedrock KB RAG Grounding | Test 5: Code Interpreter Math Sandbox |
|:---:|:---:|
| <img src="assets/Task%203%20Completion%20SS.png" alt="Test 3 Knowledge Base" width="450"/> | <img src="assets/Task%205%20Completion%20SS.png" alt="Test 5 Loyalty Calculation" width="450"/> |
| *Retrieved Platinum tier benefits: free same-day delivery, 15% discount, and priority support.* | *Deterministic arithmetic: 4,000 points ($40) + 10% Gold tier discount ($15) on $150 order = $99 final total.* |

```bash
# Test 3 — Knowledge Base (RAG)
uv run agentcore invoke '{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": "CUST-123", "session_id": "t3"}'

# Test 5 — Loyalty Discount Calculation
uv run agentcore invoke '{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", "customer_id": "CUST-123", "session_id": "t5"}'
```

---

### 3. Cross-Session Long-Term Memory Recall

Demonstrating persistent memory across separate conversational sessions (`s-A` and `s-B`) for customer `CUST-123`:

| Session A: Customer Context Ingestion | Session B: Cross-Session Recall Verification |
|:---:|:---:|
| <img src="assets/Task%204A%20Completion%20SS.png" alt="Test 4 Session A Ingestion" width="450"/> | <img src="assets/Task%204B%20Completion%20SS.png" alt="Test 4 Session B Recall" width="450"/> |
| *Session s-A: Customer introduces name ("Jane") and preference for concise communication.* | *Session s-B (30s later): Agent autonomously greets Jane and references her concise preference.* |

```bash
# Session A — Context Stored
uv run agentcore invoke '{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": "CUST-123", "session_id": "s-A"}'

# Session B — Context Recalled
uv run agentcore invoke '{"prompt": "Do you remember my name and communication preference?", "customer_id": "CUST-123", "session_id": "s-B"}'
```

---

### 4. Live Web Browsing via Playwright CDP

The agent launches an isolated browser session, navigates to external websites, and extracts live page content:

<div align="center">
  <img src="assets/Task%206%20Completion%20SS.png" alt="Test 6 Live Browser Navigation" width="750"/>
  <p><em>AgentCore Browser tool connecting over CDP, browsing live Udacity.com, and extracting: <b>"Learn the Latest Tech Skills; Advance Your Career | Udacity"</b></em></p>
</div>

```bash
# Test 6 — Browser Automation
uv run agentcore invoke '{"prompt": "Go to https://www.udacity.com and tell me the page title.", "customer_id": "CUST-123", "session_id": "t6"}'
```

---

## Task 8: Written Reflection

> Also available as [`reflection.txt`](reflection.txt) in the repository root.

### 1. Architectural Design Decision
A foundational design choice in this architecture was integrating backend tools via the Amazon Bedrock AgentCore Gateway using the Model Context Protocol (MCP). Rather than hardcoding custom HTTP client wrappers or tightly coupling REST endpoints and AWS Lambda functions inside the core agent code, the Gateway acts as a centralized tool broker exposing both API Gateway routes (`order_tracker`) and standalone Lambda functions (`refund_processor`) over a standardized streamable HTTP protocol. This decoupling enables independent evolution of business services, centralizes tool-level governance and authentication, and simplifies the agent's tool surface to a clean declarative MCP client.

### 2. Concrete Challenge & Resolution
During development, a critical challenge arose during the cloud deployment of the browser automation tool (`AgentCoreBrowser`). Packaging dependencies from a Windows development environment caused the archive (`dependencies.zip`) to omit Unix POSIX execution bits (`0o755`). When unpacked into the serverless Linux container runtime at `/var/task`, the Playwright Node.js driver binary was marked read-only (`0644`), triggering `PermissionError: [Errno 13] Permission denied: '/var/task/playwright/driver/node'`. Modifying permissions in-place failed because `/var/task` is mounted read-only. I resolved this by implementing an initialization shim in `main.py` that executes during container bootstrap: it copies the Node binary into the writable, executable `/tmp` partition, applies `chmod 0o755`, and exports the `PLAYWRIGHT_NODEJS_PATH` environment variable. This allowed Playwright to reliably launch its driver process across serverless container invocations without modifying packaging tooling.

### 3. Production Consideration
In a production deployment, managing conversation context degradation and token budgets is critical. As multi-turn sessions grow, prepending raw long-term memory context alongside complete conversation histories will eventually exhaust model context limits and inflate latency and inference costs. To address this, I would implement an automated rolling summarization hook that activates when session message history exceeds a threshold (e.g., 10 turns). Older conversation turns would be condensed into structured turn summaries while keeping recent user turns intact. Combined with semantic caching of repeated queries and least-privilege IAM scoping on tool execution roles, this ensures the support agent remains performant, cost-effective, and resilient at enterprise scale.

---

## Repository Structure

```text
AWS-Udacity-Customer-Support-Agent-Project/
├── assets/                             # Verification screenshots for all 6 test scenarios
│   ├── Task 1 Completion SS.png        # Order status verification screenshot
│   ├── Task 2 Completion SS.png        # Refund approval verification screenshot
│   ├── Task 3 Completion SS.png        # RAG knowledge base retrieval screenshot
│   ├── Task 4A Completion SS.png       # Long-term memory ingestion screenshot
│   ├── Task 4B Completion SS.png       # Cross-session recall screenshot
│   ├── Task 5 Completion SS.png        # Sandboxed code calculation screenshot
│   └── Task 6 Completion SS.png        # Live Playwright browsing verification screenshot
├── starter/                            # Core implementation source code
│   ├── lambda/                         # Backend tool services
│   │   ├── lambda_schema               # Refund tool JSON schema for Gateway
│   │   ├── order_tracker.py            # Order lookup Lambda handler (REST API)
│   │   └── refund_processor.py         # Refund issuance Lambda handler
│   ├── .bedrock_agentcore.yaml         # Deployment metadata and runtime configuration
│   ├── main.py                         # Core Agent application & Strands entrypoint
│   ├── product_catalog.txt             # Grounding knowledge base source corpus
│   ├── pyproject.toml                  # Python dependencies and package definition
│   ├── setup_permissions.py            # IAM policy configuration helper
│   └── uv.lock                         # Deterministic lockfile
├── .gitignore                          # Clean Git ignore rules (virtualenvs, zip bundles > 100MB)
├── LICENSE.txt                         # MIT Open Source License
├── reflection.txt                      # Task 8 Deliverable: Engineering reflection
└── README.md                           # Portfolio documentation & architectural deep-dive
```

---

## Setup & Deployment Guide

### Prerequisites
* **AWS Account:** In region `us-east-1` with permissions for Bedrock, AgentCore, Lambda, API Gateway, S3, and IAM.
* **Model Access:** Amazon Nova Lite (`amazon.nova-2-lite-v1:0` / `global.amazon.nova-2-lite-v1:0`) enabled in the Amazon Bedrock console.
* **Environment:** Python 3.13+, `uv`, AWS CLI v2.

### Step 1: Lambda Functions Deployment
1. Create `order-tracker` function (Python 3.12) using `starter/lambda/order_tracker.py`.
2. Create `refund-processor` function (Python 3.12) using `starter/lambda/refund_processor.py`.

### Step 2: API Gateway REST API
1. Create a REST API named `OrderTrackingAPI`.
2. Configure resources with Lambda proxy integration to `order-tracker`:
   * `GET /orders/{order_id}` (Operation: `get_order`)
   * `GET /customers/{customer_id}/orders` (Operation: `get_customer_orders`)
   * `GET /customers/{customer_id}` (Operation: `get_customer`)
3. Deploy to stage `prod`.

### Step 3: AgentCore Gateway Configuration
1. Open Amazon Bedrock → AgentCore → Gateways → Create `CustomerSupportGateway` with authorizer `NONE`.
2. Add target `order_tracker` pointing to your API Gateway `prod` stage.
3. Add target `refund_processor` pointing to Lambda `refund-processor` using schema `starter/lambda/lambda_schema`.
4. Copy the Gateway URL ending with `/mcp`.

### Step 4: Knowledge Base Creation & Sync
1. Upload `starter/product_catalog.txt` to an Amazon S3 bucket.
2. In Bedrock, create Managed Knowledge Base `CustomerSupportKB` with managed embeddings.
3. Sync the data source and copy the `KB_ID`.

### Step 5: AgentCore Memory Resource
1. In Bedrock → AgentCore → Memory, create `CustomerSupportMemory`.
2. Configure strategies:
   * `customer_facts`: `cs_agent/{actorId}/facts` (Semantic extraction)
   * `customer_preferences`: `cs_agent/{actorId}/preferences` (User preference)
3. Copy the `MEMORY_ID`.

### Step 6: Direct Code Deployment
Fill in the configuration variables in `starter/main.py`:
```python
GATEWAY_URL = "https://<alias>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "<your-kb-id>"
REGION      = "us-east-1"
MEMORY_ID   = "<your-memory-id>"
```

Deploy from `starter/`:
```bash
# Configure deployment
uv run agentcore configure --entrypoint main.py --name customer-support-agent --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory

# Deploy to AgentCore runtime
uv run agentcore deploy

# Apply IAM permissions for KB, Memory, Browser, and Code Interpreter
uv run setup_permissions.py
```

### Step 7: Verification Invocations
Run the six functional verification tests:
```powershell
# Test 1: Order Tracking
uv run agentcore invoke '{\"prompt\": \"Can you track order ORD-001?\", \"customer_id\": \"CUST-123\", \"session_id\": \"t1\"}'

# Test 2: Refund Processing
uv run agentcore invoke '{\"prompt\": \"I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.\", \"customer_id\": \"CUST-123\", \"session_id\": \"t2\"}'

# Test 3: Knowledge Base RAG
uv run agentcore invoke '{\"prompt\": \"What are the benefits of the Platinum loyalty tier?\", \"customer_id\": \"CUST-123\", \"session_id\": \"t3\"}'

# Test 4: Memory Ingestion & Recall (Wait 30s between A and B)
uv run agentcore invoke '{\"prompt\": \"Hi, I am Jane. I prefer concise responses.\", \"customer_id\": \"CUST-123\", \"session_id\": \"s-A\"}'
uv run agentcore invoke '{\"prompt\": \"Do you remember my name and communication preference?\", \"customer_id\": \"CUST-123\", \"session_id\": \"s-B\"}'

# Test 5: Discount Calculator
uv run agentcore invoke '{\"prompt\": \"I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.\", \"customer_id\": \"CUST-123\", \"session_id\": \"t5\"}'

# Test 6: Browser Live Navigation
uv run agentcore invoke '{\"prompt\": \"Go to https://www.udacity.com and tell me the page title.\", \"customer_id\": \"CUST-123\", \"session_id\": \"t6\"}'
```

### Step 8: Infrastructure Teardown & Cost Cleanup
To avoid ongoing charges on your AWS Cloud Lab allocation:
```bash
# 1. Destroy agent deployment
uv run agentcore destroy

# 2. Delete Bedrock AgentCore Gateway & Memory resources in AWS Console
# 3. Delete Bedrock Knowledge Base & OpenSearch Serverless collection
# 4. Empty and delete S3 bucket
# 5. Delete API Gateway REST API and Lambda functions
```
