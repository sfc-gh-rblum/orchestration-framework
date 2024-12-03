# Copyright 2024 Snowflake Inc.
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import json
import logging
import re
import threading
from collections.abc import Sequence
from typing import Any, Dict, List, Mapping, Optional, Union, cast

from snowflake.connector.connection import SnowflakeConnection
from snowflake.snowpark import Session

from agent_gateway.chains.chain import Chain
from agent_gateway.gateway.constants import END_OF_PLAN, FUSION_REPLAN
from agent_gateway.gateway.planner import Planner
from agent_gateway.gateway.task_processor import Task, TaskProcessor
from agent_gateway.tools.base import StructuredTool, Tool
from agent_gateway.tools.logger import gateway_logger
from agent_gateway.tools.snowflake_prompts import OUTPUT_PROMPT
from agent_gateway.tools.snowflake_prompts import (
    PLANNER_PROMPT as SNOWFLAKE_PLANNER_PROMPT,
)
from agent_gateway.tools.utils import CortexEndpointBuilder, post_cortex_request


class AgentGatewayError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class CortexCompleteAgent:
    """Self defined agent for Cortex gateway."""

    def __init__(self, session, llm) -> None:
        self.llm = llm
        self.session = session

    async def arun(self, prompt: str) -> str:
        """Run the LLM."""
        headers, url, data = self._prepare_llm_request(prompt=prompt)
        gateway_logger.log(logging.DEBUG, "Cortex Request URL\n", url, block=True)
        gateway_logger.log(logging.DEBUG, "Cortex Request Data\n", data, block=True)

        response_text = await post_cortex_request(url=url, headers=headers, data=data)
        gateway_logger.log(
            logging.DEBUG,
            "Cortex Request Response\n",
            response_text,
            block=True,
        )

        if "choices" not in response_text:
            raise AgentGatewayError(
                message=f"Failed Cortex LLM Request. Missing choices in response. See details:{response_text}"
            )

        try:
            snowflake_response = self._parse_snowflake_response(response_text)
            return snowflake_response
        except Exception:
            raise AgentGatewayError(
                message=f"Failed Cortex LLM Request. Unable to parse response. See details:{response_text}"
            )

    def _prepare_llm_request(self, prompt):
        eb = CortexEndpointBuilder(self.session)
        url = eb.get_complete_endpoint()
        headers = eb.get_complete_headers()
        data = {"model": self.llm, "messages": [{"content": prompt}]}

        return headers, url, data

    def _parse_snowflake_response(self, data_str):
        try:
            json_objects = data_str.split("\ndata: ")
            json_list = []

            # Iterate over each JSON object
            for obj in json_objects:
                obj = obj.strip()
                if obj:
                    # Remove the 'data: ' prefix if it exists
                    if obj.startswith("data: "):
                        obj = obj[6:]
                    # Load the JSON object into a Python dictionary
                    json_dict = json.loads(obj, strict=False)
                    # Append the JSON dictionary to the list
                    json_list.append(json_dict)

            completion = ""
            choices = {}
            for chunk in json_list:
                choices = chunk["choices"][0]

                if "content" in choices["delta"].keys():
                    completion += choices["delta"]["content"]

            return completion
        except KeyError as e:
            raise AgentGatewayError(
                message=f"Missing Cortex LLM response components. {str(e)}"
            )


class SummarizationAgent(Tool):
    def __init__(self, session, agent_llm):
        tool_name = "summarize"
        tool_description = "Concisely summarizes cortex search output"
        summarizer = CortexCompleteAgent(session=session, llm=agent_llm)
        super().__init__(
            name=tool_name, func=summarizer.arun, description=tool_description
        )


class Agent(Chain, extra="allow"):
    """Cortex Gateway Multi Agent Class"""

    input_key: str = "input"
    output_key: str = "output"

    def __init__(
        self,
        snowflake_connection: Union[Session, SnowflakeConnection],
        tools: list[Union[Tool, StructuredTool]],
        max_retries: int = 2,
        planner_llm: str = "mistral-large2",  # replace basellm
        agent_llm: str = "mistral-large2",  # replace basellm
        planner_example_prompt: str = SNOWFLAKE_PLANNER_PROMPT,
        planner_example_prompt_replan: Optional[str] = None,
        planner_stop: Optional[list[str]] = [END_OF_PLAN],
        fusion_prompt: str = OUTPUT_PROMPT,
        fusion_prompt_final: Optional[str] = None,
        planner_stream: bool = False,
        **kwargs,
    ) -> None:
        """Parameters

        ----------

        Args:
            snowflake_connection: authenticated Snowflake connection object
            tools: List of tools to use.
            max_retries: Maximum number of replans to do. Defaults to 2.
            planner_llm: Name of Snowflake Cortex LLM to use for planning.
            agent_llm: Name of Snowflake Cortex LLM to use for planning.
            planner_example_prompt: Example prompt for planning. Defaults to SNOWFLAKE_PLANNER_PROMPT.
            planner_example_prompt_replan: Example prompt for replanning.
                Assign this if you want to use different example prompt for replanning.
                If not assigned, default to `planner_example_prompt`.
            planner_stop: Stop tokens for planning.
            fusion_prompt: Prompt to use for fusion.
            fusion_prompt_final: Prompt to use for fusion at the final replanning iter.
                If not assigned, default to `fusion_prompt`.
            planner_stream: Whether to stream the planning.

        """
        super().__init__(name="gateway", **kwargs)

        if not planner_example_prompt_replan:
            planner_example_prompt_replan = planner_example_prompt

        summarizer = SummarizationAgent(
            session=snowflake_connection, agent_llm=agent_llm
        )
        tools_with_summarizer = tools + [summarizer]

        self.planner = Planner(
            session=snowflake_connection,
            llm=planner_llm,
            example_prompt=planner_example_prompt,
            example_prompt_replan=planner_example_prompt_replan,
            tools=tools_with_summarizer,
            stop=planner_stop,
        )

        self.agent = CortexCompleteAgent(session=snowflake_connection, llm=agent_llm)
        self.fusion_prompt = fusion_prompt
        self.fusion_prompt_final = fusion_prompt_final or fusion_prompt
        self.planner_stream = planner_stream
        self.max_retries = max_retries

        # callbacks
        self.planner_callback = None
        self.executor_callback = None
        gateway_logger.log(logging.INFO, "Cortex gateway successfully initialized")

    @property
    def input_keys(self) -> List[str]:
        return [self.input_key]

    @property
    def output_keys(self) -> List[str]:
        return [self.output_key]

    def _parse_fusion_output(self, raw_answer: str) -> str:
        """We expect the fusion output format to be:
        ```
        Thought: xxx
        Action: Finish/Replan(yyy)
        ```
        Returns:
            thought (xxx)
            answer (yyy)
            is_replan (True/False)
        """
        # Extracting the Thought
        thought_pattern = r"Thought: (.*?)\n\n"
        thought_match = re.search(thought_pattern, raw_answer)
        thought = thought_match.group(1) if thought_match else None

        # Extracting the Answer
        answer = self._extract_answer(raw_answer)
        is_replan = FUSION_REPLAN in answer

        return thought, answer, is_replan

    def _extract_answer(self, raw_answer):
        start_index = raw_answer.find("Action: Finish(")
        replan_index = raw_answer.find("Replan")
        if start_index != -1:
            start_index += len("Action: Finish(")
            parentheses_count = 1
            for i, char in enumerate(raw_answer[start_index:], start_index):
                if char == "(":
                    parentheses_count += 1
                elif char == ")":
                    parentheses_count -= 1
                    if parentheses_count == 0:
                        end_index = i
                        break
            else:
                # If no corresponding closing parenthesis is found
                return None
            answer = raw_answer[start_index:end_index]
            return answer
        else:
            if replan_index != 1:
                print("....replanning...")
                return "Replan required. Consider rephrasing your question."
            else:
                return None

    def _generate_context_for_replanner(
        self, tasks: Mapping[int, Task], fusion_thought: str
    ) -> str:
        """Formatted like this:
        ```
        1. action 1
        Observation: xxx
        2. action 2
        Observation: yyy
        ...
        Thought: fusion_thought
        ```
        """
        previous_plan_and_observations = "\n".join(
            [
                task.get_thought_action_observation(
                    include_action=True, include_action_idx=True
                )
                for task in tasks.values()
                if not task.is_fuse
            ]
        )
        fusion_thought = f"Thought: {fusion_thought}"
        context = "\n\n".join([previous_plan_and_observations, fusion_thought])
        return context

    def _format_contexts(self, contexts: Sequence[str]) -> str:
        """Contexts is a list of context
        each context is formatted as the description of _generate_context_for_replanner
        """
        formatted_contexts = ""
        for context in contexts:
            formatted_contexts += f"Previous Plan:\n\n{context}\n\n"
        formatted_contexts += "Current Plan:\n\n"
        return formatted_contexts

    async def fuse(
        self, input_query: str, agent_scratchpad: str, is_final: bool
    ) -> str:
        if is_final:
            fusion_prompt = self.fusion_prompt_final
        else:
            fusion_prompt = self.fusion_prompt
        prompt = (
            f"{fusion_prompt}\n"  # Instructions and examples
            f"Question: {input_query}\n\n"  # User input query
            f"{agent_scratchpad}\n"  # T-A-O
            # "---\n"
        )

        response = await self.agent.arun(prompt)
        raw_answer = cast(str, response)
        gateway_logger.log(logging.DEBUG, "Question: \n", input_query, block=True)
        gateway_logger.log(logging.DEBUG, "Raw Answer: \n", raw_answer, block=True)
        thought, answer, is_replan = self._parse_fusion_output(raw_answer)
        if is_final:
            # If final, we don't need to replan
            is_replan = False
        return thought, answer, is_replan

    def _call(self, inputs):
        return self.__call__(inputs)

    def __call__(self, input: str):
        """Calls Cortex gateway multi-agent system.

        Params:
            input (str): user's natural language request
        """
        result = []
        thread = threading.Thread(target=self.run_async, args=(input, result))
        thread.start()
        thread.join()
        try:
            return result[0]["output"]
        except IndexError:
            raise AgentGatewayError(
                message="Unable to retrieve response. Please check each of your Cortex tools and ensure all connections are valid."
            )

    def handle_exception(self, loop, context):
        exception = context.get("exception")
        if exception:
            print(f"Caught unhandled exception: {exception}")
            loop.default_exception_handler(context)
            loop.stop()

    def run_async(self, input, result):
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(self.handle_exception)
        asyncio.set_event_loop(loop)
        result.append(loop.run_until_complete(self.acall(input)))

    async def acall(
        self,
        input: str,
        # inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        contexts = []
        fusion_thought = ""
        agent_scratchpad = ""
        inputs = {"input": input}
        for i in range(self.max_retries):
            is_first_iter = i == 0
            is_final_iter = i == self.max_retries - 1

            task_processor = TaskProcessor()
            if self.planner_stream:
                task_queue = asyncio.Queue()
                asyncio.create_task(
                    self.planner.aplan(
                        inputs=inputs,
                        task_queue=task_queue,
                        is_replan=not is_first_iter,
                        callbacks=(
                            [self.planner_callback] if self.planner_callback else None
                        ),
                    )
                )
                await task_processor.aschedule(
                    task_queue=task_queue, func=lambda x: None
                )
            else:
                tasks = await self.planner.plan(
                    inputs=inputs,
                    is_replan=not is_first_iter,
                    callbacks=(
                        [self.planner_callback] if self.planner_callback else None
                    ),
                )

                task_processor.set_tasks(tasks)
                await task_processor.schedule()
            tasks = task_processor.tasks

            # collect thought-action-observation
            agent_scratchpad += "\n\n"
            agent_scratchpad += "".join(
                [
                    task.get_thought_action_observation(
                        include_action=True, include_thought=True
                    )
                    for task in tasks.values()
                    if not task.is_fuse
                ]
            )
            agent_scratchpad = agent_scratchpad.strip()

            fusion_thought, answer, is_replan = await self.fuse(
                input,
                agent_scratchpad=agent_scratchpad,
                is_final=is_final_iter,
            )
            if not is_replan:
                break

            # Collect contexts for the subsequent replanner
            context = self._generate_context_for_replanner(
                tasks=tasks, fusion_thought=fusion_thought
            )
            contexts.append(context)
            formatted_contexts = self._format_contexts(contexts)
            inputs["context"] = formatted_contexts

        return {self.output_key: answer}
