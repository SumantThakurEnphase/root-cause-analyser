"""
Microsoft Teams Bot — handles incoming messages and delegates to the RCA Agent.
"""

import json
import traceback

from botbuilder.core import ActivityHandler, TurnContext, MessageFactory, CardFactory
from botbuilder.schema import Activity, ActivityTypes

from agents.rca_agent import RCAAgent
from services.input_parser import extract_url_from_message


class RCABot(ActivityHandler):
    def __init__(self):
        self.rca_agent = RCAAgent()

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        """Handle incoming messages from Teams users."""
        raw_message = turn_context.activity.text or ""
        raw_message = raw_message.strip()

        if not raw_message:
            await turn_context.send_activity(
                MessageFactory.text("Please provide an error description to analyze.")
            )
            return

        # Handle special commands
        if raw_message.lower() in ("/help", "help"):
            await self._send_help(turn_context)
            return

        # Extract URL (if present) and issue description from the message
        url, query = extract_url_from_message(raw_message)

        if not query:
            query = raw_message  # Use full message as query if URL extraction consumed everything

        # Send a "thinking" indicator
        await turn_context.send_activity(
            Activity(type=ActivityTypes.typing)
        )

        status_msg = "🔍 Analyzing your error report..."
        if url:
            status_msg += f"\n📎 Detected project URL: `{url}`"
        status_msg += "\nThis may take a moment."
        await turn_context.send_activity(MessageFactory.text(status_msg))

        try:
            # Run the RCA pipeline
            rca_result = await self.rca_agent.analyze(query, url=url or "")

            # Send the result as an Adaptive Card
            card = self._build_adaptive_card(query, rca_result)
            message = MessageFactory.attachment(
                CardFactory.adaptive_card(card)
            )
            await turn_context.send_activity(message)

        except Exception as e:
            error_msg = f"❌ Error during analysis: {str(e)}"
            print(f"RCA Bot Error: {traceback.format_exc()}")
            await turn_context.send_activity(MessageFactory.text(error_msg))

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        """Welcome new members when they join the conversation."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome = (
                    "👋 **Welcome to the Root Cause Analyser Bot!**\n\n"
                    "I can help you identify the root cause of errors in the Solargraf platform.\n\n"
                    "**How to use:**\n"
                    "Simply describe the error you're investigating, for example:\n"
                    "- _\"Download DWG from SDT/EDT is failing\"_\n"
                    "- _\"Auto-design is timing out during panel placement\"_\n"
                    "- _\"3D rendering crashes with null geometry\"_\n\n"
                    "Type `/help` for more info."
                )
                await turn_context.send_activity(MessageFactory.text(welcome))

    async def _send_help(self, turn_context: TurnContext) -> None:
        help_text = (
            "🤖 **Root Cause Analyser Bot — Help**\n\n"
            "**What I do:**\n"
            "I analyze error reports by searching SigNoz logs and the Solargraf codebase "
            "(solargraf-api, graf-apps, design-tool) to identify the root cause.\n\n"
            "**How to use:**\n"
            "Send me a Solargraf project URL + describe the issue. I'll figure out which "
            "API is responsible and check the logs for that project.\n\n"
            "**Example queries:**\n"
            "- `https://app.solargraf.com/projects/342321 roofline detection not working`\n"
            "- `https://app.solargraf.com/projects/12345/proposals/abc download DWG failing`\n"
            "- `Auto-design timeout during panel placement` (no URL — query-only mode)\n\n"
            "**What I return:**\n"
            "A structured Root Cause Analysis including:\n"
            "- Cause category (Code Bug, Config Issue, Infra, etc.)\n"
            "- Root cause explanation\n"
            "- Affected file(s) and function(s)\n"
            "- Evidence from logs\n"
            "- Suggested fix\n"
            "- Confidence level"
        )
        await turn_context.send_activity(MessageFactory.text(help_text))

    def _build_adaptive_card(self, query: str, rca_result: str) -> dict:
        """Build a Teams Adaptive Card with the RCA result."""
        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "🔍 Root Cause Analysis",
                    "weight": "Bolder",
                    "size": "Large",
                    "color": "Accent",
                },
                {
                    "type": "TextBlock",
                    "text": f"**Query:** {query}",
                    "wrap": True,
                    "spacing": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": "---",
                    "spacing": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": rca_result,
                    "wrap": True,
                    "spacing": "Medium",
                    "fontType": "Default",
                },
            ],
            "padding": "Default",
        }
