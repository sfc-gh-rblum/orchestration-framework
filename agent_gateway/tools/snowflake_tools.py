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
import contextlib
import inspect
import json
import logging
import re
from typing import Any, Type, Union

import dspy
from pydantic import BaseModel, Field, ValidationError
from snowflake.connector.connection import SnowflakeConnection
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col

from agent_gateway.agents.tools import Tool
from agent_gateway.tools.logger import gateway_logger
from agent_gateway.tools.utils import (
    CortexEndpointBuilder,
    _get_connection,
    post_cortex_request,
)


class SnowflakeError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class CortexSearchTool(Tool):
    """Cortex Search tool for use with Snowflake Agent Gateway"""

    k: int = 5
    retrieval_columns: list = []
    service_name: str = ""
    connection: Union[Session, SnowflakeConnection] = None
    auto_filter: bool = False
    filter_generator: object = None

    def __init__(
        self,
        service_name,
        service_topic,
        data_description,
        retrieval_columns,
        snowflake_connection,
        auto_filter=False,
        k=5,
    ):
        """Parameters

        ----------
        service_name (str): name of the Cortex Search Service to utilize
        service_topic (str): description of content indexed by Cortex Search.
        data_description (str): description of the source data that has been indexed.
        retrieval_columns (list): list of columns to include in Cortex Search results.
        snowflake_connection (object): snowpark connection object
        auto_filter (bool): automatically generate filter based on user's query or not.
        k: number of records to include in results
        """
        tool_name = f"{service_name.lower()}_cortexsearch"
        tool_description = self._prepare_search_description(
            name=tool_name,
            service_topic=service_topic,
            data_source_description=data_description,
        )
        super().__init__(
            name=tool_name, description=tool_description, func=self.asearch
        )
        self.auto_filter = auto_filter
        self.connection = _get_connection(snowflake_connection)
        if self.auto_filter:
            self.filter_generator = SmartSearch()
            lm = dspy.Snowflake(session=self.session, model="mixtral-8x7b")
            dspy.settings.configure(lm=lm)

        self.k = k
        self.retrieval_columns = retrieval_columns
        self.service_name = service_name
        gateway_logger.log(logging.INFO, "Cortex Search Tool successfully initialized")

    def __call__(self, question) -> Any:
        return self.asearch(question)

    async def asearch(self, query):
        gateway_logger.log(logging.DEBUG, f"Cortex Search Query:{query}")
        headers, url, data = self._prepare_request(query=query)
        response_text = await post_cortex_request(url=url, headers=headers, data=data)
        response_json = json.loads(response_text)
        gateway_logger.log(logging.DEBUG, f"Cortex Search Response:{response_json}")
        try:
            return response_json["results"]
        except Exception:
            raise SnowflakeError(message=response_json["message"])

    def _prepare_request(self, query):
        eb = CortexEndpointBuilder(self.connection)
        headers = eb.get_search_headers()
        url = eb.get_search_endpoint(
            self.connection.database,
            self.connection.schema,
            self.service_name,
        )
        if self.auto_filter:
            search_attributes, sample_vals = self._get_sample_values(
                snowflake_connection=Session.builder.config(
                    "connection", self.connection
                ),
                cortex_search_service=self.service_name,
            )
            raw_filter = self.filter_generator(
                query=query,
                attributes=str(search_attributes),
                sample_values=str(sample_vals),
            )["answer"]
            filter = json.loads(raw_filter)
        else:
            filter = None

        data = {
            "query": query,
            "columns": self.retrieval_columns,
            "limit": self.k,
            "filter": filter,
        }

        return headers, url, data

    def _prepare_search_description(self, name, service_topic, data_source_description):
        base_description = f""""{name}(query: str) -> list:\n
                 - Executes a search for relevant information about {service_topic}.\n
                 - Returns a list of relevant passages from {data_source_description}.\n"""

        return base_description

    def _get_search_attributes(self, search_service_name):
        snowflake_connection = Session.builder.config("connection", self.connection)
        df = snowflake_connection.sql("SHOW CORTEX SEARCH SERVICES")
        raw_atts = (
            df.where(col('"name"') == search_service_name)
            .select('"attribute_columns"')
            .to_pandas()
            .loc[0]
            .values[0]
        )
        attribute_list = raw_atts.split(",")

        return attribute_list

    def _get_search_table(self, search_service_name):
        snowflake_connection = Session.builder.config("connection", self.connection)
        df = snowflake_connection.sql("SHOW CORTEX SEARCH SERVICES")
        table_def = (
            df.where(col('"name"') == search_service_name)
            .select('"definition"')
            .to_pandas()
            .loc[0]
            .values[0]
        )

        pattern = r"FROM\s+([\w\.]+)"
        if match := re.search(pattern, table_def):
            return match[1]
        else:
            print("No match found.")

        return table_def

    def _get_sample_values(
        self, snowflake_connection, cortex_search_service, max_samples=10
    ):
        sample_values = {}
        attributes = self._get_search_attributes(
            snowflake_connection=snowflake_connection,
            search_service_name=cortex_search_service,
        )
        table_name = self._get_search_table(
            snowflake_connection=snowflake_connection,
            search_service_name=cortex_search_service,
        )

        for attribute in attributes:
            query = f"""SELECT DISTINCT({attribute}) FROM {table_name} LIMIT {max_samples}"""
            sample_values[attribute] = list(
                snowflake_connection.sql(query).to_pandas()[attribute].values
            )

        return attributes, sample_values


def get_min_length(model: Type[BaseModel]):
    min_length = 0
    for key, field in model.model_fields.items():
        if issubclass(field.annotation, BaseModel):
            min_length += get_min_length(field.annotation)
        min_length += len(key)
    return min_length


class JSONFilter(BaseModel):
    answer: str = Field(description="The filter_query in valid JSON format")

    @classmethod
    def model_validate_json(
        cls,
        json_data: str,
        *,
        strict: bool | None = None,
        context: dict[str, Any] | None = None,
    ):
        __tracebackhide__ = True
        try:
            return cls.__pydantic_validator__.validate_json(
                json_data, strict=strict, context=context
            )
        except ValidationError:
            min_length = get_min_length(cls)
            for substring_length in range(len(json_data), min_length - 1, -1):
                for start in range(len(json_data) - substring_length + 1):
                    substring = json_data[start : start + substring_length]
                    with contextlib.suppress(ValidationError):
                        return cls.__pydantic_validator__.validate_json(
                            substring, strict=strict, context=context
                        )
        raise ValueError("Could not find valid json")


class GenerateFilter(dspy.Signature):
    """Given a query, attributes in the data, and example values of each attribute, generate a filter in valid JSON format.
    Ensure the filter only uses valid operators: @eq, @contains,@and,@or,@not
    Ensure only the valid JSON is output with no other reasoning.

    ---
    Query: What was the sentiment of CEOs between 2021 and 2024?
    Attributes: industry,hq,date
    Sample Values: {"industry":["biotechnology","healthcare","agriculture"],"HQ":["NY, US","CA,US","FL,US"],"date":["01/01,1999","01/01/2024"]}
    Answer: {"@or":[{"@eq":{"year":"2021"}},{"@eq":{"year":"2022"}},{"@eq":{"year":"2023"}},{"@eq":{"year":"2024"}}]}

    Query: What is the sentiment of Biotech CEOs of companies based in New York?
    Attributes: industry,hq,date
    Sample Values: {"industry":["biotechnology","healthcare","agriculture"],"HQ":["NY, US","CA,US","FL,US"],"date":["01/01,1999","01/01/2024"]}
    Answer: {"@and":[{ "@eq": { "industry": "biotechnology" } },{"@not":{"@eq":{"HQ":"CA,US"}}}]}

    Query: What is the sentiment of Biotech CEOs outside of California?
    Attributes: industry,hq,date
    Sample Values: {"industry":["biotechnology","healthcare","agriculture"],"HQ":["NY, US","CA,US","FL,US"],"date":["01/01,1999","01/01/2024"]}
    Answer: {"@and":[{ "@eq": { "industry": "biotechnology" } },{"@not":{"@eq":{"HQ":"CA,US"}}}]}

    Query: What is sentiment towards ag and biotech companies based outside of the US?
    Attributes: industry,hq,date
    Sample Values: {"industry":["biotechnology","healthcare","agriculture"],"COUNTRY":["United States","Ireland","Russia","Georgia","Spain"],"month":["01","02","03","06","11","12"],"year":["2022","2023","2024"]}
    Answer: {"@and": [{ "@or": [{"@eq":{ "industry": "biotechnology" } },{"@eq":{"industry":"agriculture"}}]},{ "@not": {"@eq": { "COUNTRY": "United States" } }}]}
    """

    query = dspy.InputField(desc="user query")
    attributes = dspy.InputField(desc="attributes to filter on")
    sample_values = dspy.InputField(desc="examples of values per attribute")
    answer: JSONFilter = dspy.OutputField(
        desc="filter query in valid JSON format. ONLY output the filter query in JSON, no reasoning"
    )


class SmartSearch(dspy.Module):
    def __init__(self):
        super().__init__()
        self.filter_gen = dspy.ChainOfThought(GenerateFilter)

    def forward(self, query, attributes, sample_values):
        filter_query = self.filter_gen(
            query=query, attributes=attributes, sample_values=sample_values
        )

        return filter_query


class CortexAnalystTool(Tool):
    """""Cortex Analyst tool for use with Snowflake Agent Gateway""" ""

    STAGE: str = ""
    FILE: str = ""
    connection: Union[Session, SnowflakeConnection] = None

    def __init__(
        self,
        semantic_model,
        stage,
        service_topic,
        data_description,
        snowflake_connection,
    ):
        """Parameters

        ----------
        semantic_model (str): yaml file name containing semantic model for Cortex Analyst
        stage (str): name of stage containing semantic model yaml.
        service_topic (str): topic of the data in the tables (i.e S&P500 company financials).
        data_description (str): description of the source data that has been indexed (i.e a table with stock and financial metrics about S&P500 companies).
        snowflake_connection (object): snowpark connection object
        """
        tname = semantic_model.replace(".yaml", "") + "_" + "cortexanalyst"
        tool_description = self._prepare_analyst_description(
            name=tname,
            service_topic=service_topic,
            data_source_description=data_description,
        )

        super().__init__(name=tname, func=self.asearch, description=tool_description)
        self.connection = _get_connection(snowflake_connection)
        self.FILE = semantic_model
        self.STAGE = stage

        gateway_logger.log(logging.INFO, "Cortex Analyst Tool successfully initialized")

    def __call__(self, prompt) -> Any:
        return self.asearch(query=prompt)

    async def asearch(self, query):
        gateway_logger.log(logging.DEBUG, f"Cortex Analyst Prompt:{query}")

        for _ in range(3):
            current_query = query
            url, headers, data = self._prepare_analyst_request(prompt=query)

            response_text = await post_cortex_request(
                url=url, headers=headers, data=data
            )
            json_response = json.loads(response_text)

            gateway_logger.log(
                logging.DEBUG, f"Cortex Analyst Raw Response:{json_response}"
            )

            try:
                query_response = self._process_analyst_message(
                    json_response["message"]["content"]
                )

                if "Unable to generate valid SQL Query" in query_response:
                    lm = dspy.Snowflake(
                        session=Session.builder.config(
                            "connection", self.connection
                        ).getOrCreate(),
                        model="llama3.2-1b",
                    )
                    dspy.settings.configure(lm=lm)
                    rephrase_prompt = dspy.ChainOfThought(PromptRephrase)
                    prompt = f"Original Query: {current_query}. Previous Response Context: {query_response}"
                    current_query = rephrase_prompt(user_prompt=prompt)[
                        "rephrased_prompt"
                    ]
                else:
                    break

            except Exception:
                raise SnowflakeError(message=json_response["message"])

        return query_response

    def _prepare_analyst_request(self, prompt):
        data = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}]}
            ],
            "semantic_model_file": f"@{self.connection.database}.{self.connection.schema}.{self.STAGE}/{self.FILE}",
        }

        eb = CortexEndpointBuilder(self.connection)
        headers = eb.get_analyst_headers()
        url = eb.get_analyst_endpoint()

        return url, headers, data

    def _process_analyst_message(self, response):
        if isinstance(response, list) and len(response) > 0:
            first_item = response[0]

            if "type" in first_item:
                if first_item["type"] == "text":
                    _ = None
                    for item in response:
                        _ = item
                        if item["type"] == "suggestions":
                            raise SnowflakeError(
                                message=f"Your request is unclear. Consider rephrasing your request to one of the following suggestions:{item['suggestions']}"
                            )
                        elif item["type"] == "sql":
                            sql_query = item["statement"]
                            table = (
                                self.connection.cursor()
                                .execute(sql_query)
                                .fetch_arrow_all()
                            )

                            if table is not None:
                                return str(table.to_pydict())
                            else:
                                raise SnowflakeError(
                                    message="No results found. Consider rephrasing your request"
                                )

                    raise SnowflakeError(
                        message=f"Unable to generate a valid SQL Query. {_['text']}"
                    )

        return SnowflakeError(message="Invalid Cortex Analyst Response")

    def _prepare_analyst_description(
        self, name, service_topic, data_source_description
    ):
        base_analyst_description = f"""{name}(prompt: str) -> str:\n
                  - takes a user's question about {service_topic } and queries {data_source_description}\n
                  - Returns the relevant metrics about {service_topic}\n"""

        return base_analyst_description


class PromptRephrase(dspy.Signature):
    """Takes in a prompt and rephrases it using context into to a single concise, and specific question.
    If there are references to entities that are not clear or consistent with the question being asked, make the references more appropriate.
    """

    user_prompt = dspy.InputField(desc="original user prompt")
    rephrased_prompt = dspy.OutputField(
        desc="rephrased prompt with more clear and specific intent"
    )


class PythonTool(Tool):
    python_callable: object = None

    def __init__(self, python_func, tool_description, output_description) -> None:
        python_callable = self.asyncify(python_func)
        desc = self._generate_description(
            python_func=python_func,
            tool_description=tool_description,
            output_description=output_description,
        )
        super().__init__(
            name=python_func.__name__, func=python_callable, description=desc
        )
        self.python_callable = python_func
        gateway_logger.log(logging.INFO, "Python Tool successfully initialized")

    def asyncify(self, sync_func):
        async def async_func(*args, **kwargs):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, sync_func, *args, **kwargs)

        return async_func

    def _generate_description(self, python_func, tool_description, output_description):
        full_sig = self._process_full_signature(python_func=python_func)
        return f"""{full_sig}\n - {tool_description}\n - {output_description}"""

    def _process_full_signature(self, python_func):
        name = python_func.__name__
        signature = str(inspect.signature(python_func))
        return name + signature
