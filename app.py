import json
import logging
import os
import re
import time
from datetime import timedelta
from typing import Any

from dotenv import load_dotenv
from langchain.callbacks.base import BaseCallbackHandler
# from langchain.chat_models import ChatOpenAI
from langchain_openai import AzureChatOpenAI

# from langchain.memory import MomentoChatMessageHistory
from langchain_community.chat_message_histories import MomentoChatMessageHistory

from langchain.schema import HumanMessage, LLMResult, SystemMessage
from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
from slack_bolt.adapter.socket_mode import SocketModeHandler

from datetime import timedelta

CHAT_UPDATE_INTERVAL_SEC = 1

load_dotenv()

app = App(token=os.environ.get("SLACK_BOT_TOKENN"),
          signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
          process_before_response=True,
          )

class SlackStreamingCallbackHandler(BaseCallbackHandler):
    last_send_time = time.time()
    message = ""

    def __init__(self, channel, ts):
        self.channel = channel
        self.ts = ts
        self.interval = CHAT_UPDATE_INTERVAL_SEC
        self.update_count = 0

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.message += token

        now = time.time()
        if now - self.last_send_time > CHAT_UPDATE_INTERVAL_SEC:
            self.last_send_time = now
            app.client.chat_update(
                channel=self.channel, ts=self.ts, text=f"{self.message}..."
            )
            self.last_send_time = now
            self.update_count += 1
            if self.update_count / 10 > self.interval:
                self.interval = self.interval * 2

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        message_context =  "OpenAI APIで生成される情報は不正確または不適切な場合がありますが当社の見解を述べるものではありません。"
        message_blocks =  [
            {"type": "section", "text": {"type": "mrkdwn", "text": self.message}},
            {"type": "divider"},
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": message_context}],
            },
        ]
        app.client.chat_update(
            channel=self.channel,
            ts=self.ts,
            text=self.message,
            blocks=message_blocks,
        )


# @app.event("app_mention")
def handle_mention(event, say):
    user = event["user"]
    thread_ts = event["ts"]
    channel = event["channel"]
    message = re.sub("<@.*>", "", event["text"])

    id_ts = event["ts"]
    if "thread_ts" in event:
        id_ts = event["thread_ts"]

    result = say("\n\nTyping...", thread_ts=thread_ts)
    ts = result["ts"]

    history = MomentoChatMessageHistory.from_client_params(
        id_ts,
        os.environ["MOMENTO_CACHE"],
        timedelta(hours=int(os.environ["MOMENTO_TTL"])),
    )

    messages = [SystemMessage(content="You are a good assistant.")]
    messages.extend(history.messages)
    messages.append(HumanMessage(content=message))
    history.add_user_message(message)

    callback = SlackStreamingCallbackHandler(channel=channel, ts=ts)
    llm = AzureChatOpenAI(
        openai_api_version=os.environ["OPENAI_API_VERSION"],
        azure_deployment=os.environ["AZURE_OPENAI_DEPLOY_NAME"],
        temperature=os.environ["OPENAI_API_TEMPERATURE"],
        streaming=True,
        callbacks=[callback],
    )
    response = llm.invoke(messages)
    history.add_message(response)
    # say(thread_ts=thread_ts, text=response.content)

def just_ack(ack):
    ack()

app.event("app_mention")(ack=just_ack, lazy=[handle_mention])

# ソケットモードハンドラーを使ってアプリを起動します
if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
