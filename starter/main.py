"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the project instructions and rubric for guidance.
Work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
import argparse
import asyncio
import inspect
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Any, Dict, List, Optional
import uuid

import boto3
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.code_interpreter_client import code_session
from mcp.client.streamable_http import streamable_http_client
from strands import Agent, tool
from strands.hooks import (
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from strands_tools.browser import AgentCoreBrowser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSAI_Agent")
logger.setLevel(logging.INFO)


# ── Playwright Linux Runtime Shim ─────────────────────────────────────────────
# Fix Playwright Node binary permissions on Linux container runtimes (resolves
# Windows zip deployment issue where files in /var/task lack POSIX execute bits).
if sys.platform != "win32":
    try:
        import playwright
        driver_dir = Path(inspect.getfile(playwright)).parent / "driver"
        node_candidates = [
            driver_dir / "node",
            Path("/var/task/playwright/driver/node"),
            Path("/app/playwright/driver/node"),
        ]
        node_src = next((p for p in node_candidates if p.exists()), None)
        if node_src:
            tmp_node = Path("/tmp/playwright-node")
            if not tmp_node.exists() or tmp_node.stat().st_size != node_src.stat().st_size:
                shutil.copy2(node_src, tmp_node)
                tmp_node.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)  # 0o755
            os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(tmp_node)
            logger.info("Configured Playwright node binary at: %s", tmp_node)
    except Exception as exc:
        logger.warning("Could not setup Playwright node shim: %s", exc)


# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
app = BedrockAgentCoreApp()

# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
GATEWAY_URL = "https://customersupportgateway-pnhgzdvzbq.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "VYHH4KS2OL"
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-zB32bJ3Qn0"


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
model_id = "global.amazon.nova-2-lite-v1:0"

model = BedrockModel(model_id=model_id)
memory_client = MemoryClient(region_name=REGION)
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
_NAMESPACES_CACHE: Dict[str, str] = {}


def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict[str, str]:
    """Return a dict mapping strategy type → namespace template string."""
    global _NAMESPACES_CACHE
    if _NAMESPACES_CACHE:
        return _NAMESPACES_CACHE

    namespaces: Dict[str, str] = {}
    strategies_response = mem_client.get_memory_strategies(memory_id)

    # Handle dict response format
    if isinstance(strategies_response, dict):
        strategies = strategies_response.get("memoryStrategies", strategies_response.get("strategies", []))
    else:
        strategies = strategies_response

    for strategy in strategies:
        s_type = strategy.get("strategyType", strategy.get("type", "UNKNOWN"))
        templates = strategy.get("namespaceTemplates") or strategy.get("namespaces") or []
        if templates and isinstance(templates, list):
            namespaces[s_type] = templates[0]

    _NAMESPACES_CACHE = namespaces
    return _NAMESPACES_CACHE


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(self.memory_client, self.memory_id)

    @staticmethod
    def _get_role(msg: Any) -> Optional[str]:
        if isinstance(msg, dict):
            return msg.get("role")
        return getattr(msg, "role", None)

    @staticmethod
    def _get_text(msg: Any) -> str:
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    texts.append(block["text"])
                elif hasattr(block, "text"):
                    texts.append(block.text)
                elif isinstance(block, str):
                    texts.append(block)
            return "\n".join(texts)
        return ""

    @staticmethod
    def _extract_memory_text(record: Any) -> str:
        """Extract clean text content from a memory record summary."""
        if not isinstance(record, dict):
            return str(record) if record else ""
        content = record.get("content")
        if isinstance(content, dict):
            return content.get("text", "")
        if isinstance(content, str):
            return content
        return record.get("text", "")

    @staticmethod
    def _set_text(msg: Any, new_text: str) -> None:
        """Safely update message text whether Converse dict or Strands object."""
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                if content and isinstance(content[0], dict) and "text" in content[0]:
                    content[0]["text"] = new_text
                elif content and hasattr(content[0], "text"):
                    content[0].text = new_text
                else:
                    msg["content"] = [{"text": new_text}]
            else:
                msg["content"] = new_text
        else:
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                if content and hasattr(content[0], "text"):
                    content[0].text = new_text
                elif content and isinstance(content[0], dict) and "text" in content[0]:
                    content[0]["text"] = new_text
                else:
                    msg.content = [{"text": new_text}]
            else:
                setattr(msg, "content", new_text)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        msg = getattr(event, "message", None)
        if msg is None:
            messages = getattr(event.agent, "messages", [])
            if not messages:
                return
            msg = messages[-1]

        # Process only user messages
        if self._get_role(msg) != "user":
            return

        query = self._get_text(msg)
        if not query or "Customer Context:" in query:
            return

        all_memories = []
        for s_type, ns_template in self.namespaces.items():
            ns = ns_template.format(actorId=self.actor_id)
            try:
                results = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=ns,
                    query=query,
                    top_k=5,
                )
                memories = results.get("memories", results.get("memoryRecordSummaries", [])) if isinstance(results, dict) else results
                for memory in (memories or []):
                    text = self._extract_memory_text(memory)
                    if text:
                        all_memories.append(f"[{s_type}] {text}")
            except Exception as e:
                logger.warning(f"Memory retrieval failed for {ns}: {e}")

        if all_memories:
            mem_text = "\n".join(all_memories)
            new_text = f"Customer Context:\n{mem_text}\n\n{query}"
            self._set_text(msg, new_text)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        user_query = None
        agent_response = None

        messages = getattr(event.agent, "messages", []) if hasattr(event, "agent") else []
        for msg in reversed(messages):
            role = self._get_role(msg)
            text = self._get_text(msg)
            if role == "assistant" and text and not agent_response:
                agent_response = text
            elif role == "user" and text and not user_query:
                user_query = text

            if user_query and agent_response:
                break

        if user_query and agent_response:
            # Strip customer context prefix before persisting
            if "Customer Context:\n" in user_query:
                parts = user_query.split("\n\n", 1)
                if len(parts) > 1:
                    user_query = parts[-1]

            try:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[(user_query, "USER"), (agent_response, "ASSISTANT")],
                )
            except Exception as e:
                logger.error(f"Error saving to memory: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query}
        )
        results = resp.get("retrievalResults", [])
        if not results:
            return "No relevant information found in the knowledge base."

        texts = [r.get("content", {}).get("text", "") for r in results]
        return "\n---\n".join(filter(None, texts))
    except Exception as e:
        return f"Error retrieving from knowledge base: {str(e)}"


# ── TODO 7 — Sandboxed Code Interpreter Tool ──────────────────────────────────
@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price with points_redeemed, tier_discount_pct, final_total, remaining_points.
    """
    code = f"""
import math, json

points = {loyalty_points}
tier = "{tier}"
total = {order_total}
category = "{product_category}"

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

points_redeemed = math.floor(points / 500) * 500
point_discount = points_redeemed / 100

max_point_discount = total * 0.5
if point_discount > max_point_discount:
    point_discount = max_point_discount
    points_redeemed = int(point_discount * 100)

tier_rate = tier_rates.get(tier, 0.0)
tier_discount = (total - point_discount) * tier_rate
total_discount = point_discount + tier_discount
final_total = max(0.0, total - total_discount)
remaining_points = points - points_redeemed

result = {{
    "points_redeemed": points_redeemed,
    "point_discount": round(point_discount, 2),
    "tier_discount_pct": int(tier_rate * 100),
    "tier_discount": round(tier_discount, 2),
    "final_total": round(final_total, 2),
    "remaining_points": remaining_points
}}
print(json.dumps(result))
"""

    tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
    tier_rate = tier_rates.get(tier, 0.0)

    try:
        with code_session(REGION) as session:
            response = session.invoke("executeCode", {"language": "python", "code": code, "clearContext": True})

            output_texts = []
            if isinstance(response, dict) and "stream" in response:
                for event in response["stream"]:
                    if "result" in event:
                        res = event["result"]
                        for item in res.get("content", []):
                            if item.get("type") == "text":
                                output_texts.append(item.get("text", ""))
                        if "stdout" in res:
                            output_texts.append(res["stdout"])
                    elif "stdout" in event:
                        output_texts.append(event["stdout"])

            if output_texts:
                return "\n".join(output_texts).strip()
            return json.dumps(response, default=str)

    except Exception as e:
        logger.warning(f"Code interpreter unavailable, using fallback: {e}")
        tier_discount = round(order_total * tier_rate, 2)
        final_total = round(order_total - tier_discount, 2)
        return json.dumps({
            "fallback": True,
            "points_redeemed": 0,
            "tier_discount_pct": int(tier_rate * 100),
            "tier_discount": tier_discount,
            "final_total": final_total,
            "remaining_points": loyalty_points,
            "note": "Code interpreter unavailable; computed tier-only discount."
        })


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are an AI customer support agent for an e-commerce platform. "
    "You have tools to track orders, process refunds, calculate discounts, "
    "search the knowledge base, and browse the web. Be polite, concise, and helpful. "
    "When using context from past memories, incorporate it naturally without breaking character."
)


def _extract_response_text(response: Any) -> str:
    """Safely extract output string from Strands agent response."""
    if hasattr(response, "text") and response.text:
        return response.text
    if hasattr(response, "message") and response.message:
        text = MemoryHook._get_text(response.message)
        if text:
            return text
    if hasattr(response, "messages") and response.messages:
        last_msg = response.messages[-1]
        text = MemoryHook._get_text(last_msg)
        if text:
            return text
    return str(response)


@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    try:
        user_input = payload.get("prompt")
        actor_id = payload.get("customer_id", "anonymous")
        session_id = payload.get("session_id", str(uuid.uuid4()))

        memory_hook = MemoryHook(actor_id, session_id, memory_client, MEMORY_ID)
        agent_core_browser = AgentCoreBrowser(region=REGION)

        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        gateway_client = MCPClient(
            lambda: streamable_http_client(url=GATEWAY_URL)
        )

        try:
            with gateway_client:
                gateway_loaded = False
                try:
                    gateway_tools = gateway_client.list_tools_sync()
                    tools.extend(gateway_tools)

                    logger.info(
                        "Gateway connected successfully. Loaded %d tools.",
                        len(gateway_tools),
                    )
                    gateway_loaded = True

                except TimeoutError:
                    logger.exception("Gateway tool loading timed out")

                except ConnectionError:
                    logger.exception("Gateway connection failed")

                except Exception as exc:
                    logger.exception(
                        "Gateway tool loading failed: %s", exc
                    )

                active_prompt = SYSTEM_PROMPT
                if not gateway_loaded:
                    active_prompt += (
                        "\n\nIMPORTANT: The external order tracking and refund gateway service is currently unavailable. "
                        "If the customer asks to track an order, check order status, or request/process a refund, "
                        "politely explain that the order and refund system is temporarily undergoing maintenance, "
                        "and advise them to check back shortly or reach out to customer support. Do not expose internal technical errors or secrets."
                    )

                agent = Agent(
                    model=model,
                    system_prompt=active_prompt,
                    tools=tools,
                    hooks=[memory_hook],
                    state={
                        "actor_id": actor_id,
                        "session_id": session_id,
                    },
                )

                response = await agent.invoke_async(user_input)
                return _extract_response_text(response)

        except TimeoutError:
            logger.exception("Gateway tool loading timed out")
        except ConnectionError:
            logger.exception("Gateway connection failed")
        except Exception as exc:
            logger.exception(
                "Gateway tool loading failed: %s", exc
            )

        # Fallback if Gateway connection failed during client initialization
        fallback_prompt = (
            SYSTEM_PROMPT +
            "\n\nIMPORTANT: The external order tracking and refund gateway service is currently unavailable. "
            "If the customer asks to track an order, check order status, or request/process a refund, "
            "politely explain that the order and refund system is temporarily undergoing maintenance, "
            "and advise them to check back shortly or reach out to customer support. Do not expose internal technical errors or secrets."
        )

        agent = Agent(
            model=model,
            system_prompt=fallback_prompt,
            tools=tools,
            hooks=[memory_hook],
            state={
                "actor_id": actor_id,
                "session_id": session_id,
            },
        )

        response = await agent.invoke_async(user_input)
        return _extract_response_text(response)

    except Exception as e:
        logger.exception("Execution Error: %s", e)
        return (
            "I apologize, but I am currently experiencing technical difficulties. "
            "Please try again in a few moments or contact support at support@example.com."
        )



# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)

if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()