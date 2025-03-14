from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any, Dict, List, Type, Union

from pydantic import BaseModel
from snowflake.connector.connection import SnowflakeConnection
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col

from agent_gateway.tools.logger import gateway_logger
from agent_gateway.tools.tools import Tool
from agent_gateway.tools.utils import (
    CortexEndpointBuilder,
    _get_connection,
    post_cortex_request,
    _determine_runtime,
)


class SnowflakeError(Exception):
    def __init__(self, message: str):
        self.message = message
        gateway_logger.log("ERROR", message)
        super().__init__(self.message)


class CortexSearchTool(Tool):
    """Cortex Search tool for use with Snowflake Agent Gateway"""

    k: int = 5
    retrieval_columns: List[str] = []
    service_name: str = ""
    connection: Union[Session, SnowflakeConnection] = None

    def __init__(
        self,
        service_name: str,
        service_topic: str,
        data_description: str,
        retrieval_columns: List[str],
        snowflake_connection: Union[Session, SnowflakeConnection],
        k: int = 5,
    ):
        """Initialize CortexSearchTool with parameters."""
        tool_name = f"{service_name.lower()}_cortexsearch"
        tool_description = self._prepare_search_description(
            name=tool_name,
            service_topic=service_topic,
            data_source_description=data_description,
        )
        super().__init__(
            name=tool_name, description=tool_description, func=self.asearch
        )
        self.connection = _get_connection(snowflake_connection)
        self.k = k
        self.retrieval_columns = retrieval_columns
        self.service_name = service_name
        gateway_logger.log("INFO", "Cortex Search Tool successfully initialized")

    def __call__(self, question: str) -> Any:
        return self.asearch(question)

    async def asearch(self, query: str) -> Dict[str, Any]:
        gateway_logger.log("DEBUG", f"Cortex Search Query: {query}")
        headers, url, data = self._prepare_request(query=query)
        response_text = await post_cortex_request(url=url, headers=headers, data=data)

        response_json = json.loads(response_text)

        try:
            if _determine_runtime():
                search_response = json.loads(response_json["content"])["results"]
            else:
                search_response = response_json["results"]
        except KeyError:
            raise SnowflakeError(
                message=f"unable to parse Cortex Search response {search_response.get('message', 'Unknown error')}"
            )

        search_col = self._get_search_column(self.service_name)
        citations = self._get_citations(search_response, search_col)

        gateway_logger.log("DEBUG", f"Cortex Search Response: {search_response}")

        return {
            "output": search_response,
            "sources": {
                "tool_type": "cortex_search",
                "tool_name": self.name,
                "metadata": citations,
            },
        }

    def _prepare_request(self, query: str) -> tuple:
        eb = CortexEndpointBuilder(self.connection)
        headers = eb.get_search_headers()
        url = eb.get_search_endpoint(
            self.connection.database,
            self.connection.schema,
            self.service_name,
        )

        data = {
            "query": query,
            "columns": self.retrieval_columns,
            "limit": self.k,
        }

        return headers, url, data

    def _get_citations(
        self, raw_response: List[Dict[str, Any]], search_column: List[str]
    ) -> List[Dict[str, Any]]:
        citation_elements = [
            {k: v for k, v in d.items() if k and k not in search_column}
            for d in raw_response
        ]

        if len(citation_elements[0].keys()) < 1:
            return [{"Search Tool": self.service_name}]

        seen = set()
        citations = []
        for c in citation_elements:
            identifier = tuple(sorted(c.items()))
            if identifier not in seen:
                seen.add(identifier)
                citations.append(c)

        return citations

    def _prepare_search_description(
        self, name: str, service_topic: str, data_source_description: str
    ) -> str:
        return (
            f""""{name}(query: str) -> list:\n"""
            f""" - Executes a search for relevant information about {service_topic}.\n"""
            f""" - Returns a list of relevant passages from {data_source_description}.\n"""
        )

    def _get_search_column(self, search_service_name: str) -> List[str]:
        return self._get_search_service_attribute(search_service_name, "search_column")

    def _get_search_attributes(self, search_service_name: str) -> List[str]:
        return self._get_search_service_attribute(
            search_service_name, "attribute_columns"
        )

    def _get_search_service_attribute(
        self, search_service_name: str, attribute: str
    ) -> List[str]:
        snowflake_connection = Session.builder.config(
            "connection", self.connection
        ).getOrCreate()
        df = snowflake_connection.sql("SHOW CORTEX SEARCH SERVICES")
        raw_atts = (
            df.where(col('"name"') == search_service_name)
            .select(f'"{attribute}"')
            .to_pandas()
            .loc[0]
            .values[0]
        )
        return raw_atts.split(",")

    def _get_search_table(self, search_service_name: str) -> str:
        snowflake_connection = Session.builder.config(
            "connection", self.connection
        ).getOrCreate()
        df = snowflake_connection.sql("SHOW CORTEX SEARCH SERVICES")
        table_def = (
            df.where(col('"name"') == search_service_name)
            .select('"definition"')
            .to_pandas()
            .loc[0]
            .values[0]
        )

        pattern = r"FROM\s+([\w\.]+)"
        match = re.search(pattern, table_def)
        return match[1] if match else "No match found."

    def _get_sample_values(
        self,
        snowflake_connection: Union[Session, SnowflakeConnection],
        cortex_search_service: str,
        max_samples: int = 10,
    ) -> tuple:
        attributes = self._get_search_attributes(cortex_search_service)
        table_name = self._get_search_table(cortex_search_service)

        sample_values = {
            attribute: list(
                snowflake_connection.sql(
                    f"SELECT DISTINCT({attribute}) FROM {table_name} LIMIT {max_samples}"
                )
                .to_pandas()[attribute]
                .values
            )
            for attribute in attributes
        }

        return attributes, sample_values


def get_min_length(model: Type[BaseModel]) -> int:
    min_length = 0
    for key, field in model.model_fields.items():
        if issubclass(field.annotation, BaseModel):
            min_length += get_min_length(field.annotation)
        min_length += len(key)
    return min_length


class CortexAnalystTool(Tool):
    """Cortex Analyst tool for use with Snowflake Agent Gateway"""

    STAGE: str = ""
    FILE: str = ""
    connection: Union[Session, SnowflakeConnection] = None

    def __init__(
        self,
        semantic_model: str,
        stage: str,
        service_topic: str,
        data_description: str,
        snowflake_connection: Union[Session, SnowflakeConnection],
    ):
        """Initialize CortexAnalystTool with parameters."""
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

        gateway_logger.log("INFO", "Cortex Analyst Tool successfully initialized")

    def __call__(self, prompt: str) -> Any:
        return self.asearch(query=prompt)

    async def asearch(self, query: str) -> Dict[str, Any]:
        gateway_logger.log("DEBUG", f"Cortex Analyst Prompt: {query}")

        url, headers, data = self._prepare_analyst_request(prompt=query)

        response_text = await post_cortex_request(url=url, headers=headers, data=data)
        json_response = json.loads(response_text)

        gateway_logger.log("DEBUG", f"Cortex Analyst Raw Response: {json_response}")

        try:
            if _determine_runtime() and isinstance(json_response["content"], str):
                json_response["content"] = json.loads(json_response["content"])
                query_response = self._process_analyst_message(
                    json_response["content"]["message"]["content"]
                )
            else:
                x = json_response["message"]["content"]
                query_response = self._process_analyst_message(x)

            return query_response

        except KeyError:
            raise SnowflakeError(message=json_response.get("message", "Unknown error"))

    def _prepare_analyst_request(self, prompt: str) -> tuple:
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

    def _process_analyst_message(
        self, response: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if response and isinstance(response, list):
            for item in response:
                if item["type"] == "suggestions":
                    raise SnowflakeError(
                        message=f"Your request is unclear. Consider rephrasing your request to one of the following suggestions: {item['suggestions']}"
                    )
                elif item["type"] == "sql":
                    sql_query = item["statement"]
                    table = (
                        self.connection.cursor().execute(sql_query).fetch_arrow_all()
                    )

                    if table:
                        tables = self._extract_tables(sql_query)
                        return {
                            "output": str(table.to_pydict()),
                            "sources": {
                                "tool_type": "cortex_analyst",
                                "tool_name": self.name,
                                "metadata": tables,
                            },
                        }
                    else:
                        raise SnowflakeError(
                            message="No results found. Consider rephrasing your request"
                        )

            raise SnowflakeError(
                message=f"Unable to generate a valid SQL Query. {response[0]['text']}"
            )

        raise SnowflakeError(message="Invalid Cortex Analyst Response")

    def _prepare_analyst_description(
        self, name: str, service_topic: str, data_source_description: str
    ) -> str:
        return (
            f"""{name}(prompt: str) -> str:\n"""
            f""" - takes a user's question about {service_topic} and queries {data_source_description}\n"""
            f""" - Returns the relevant metrics about {service_topic}\n"""
        )

    def _extract_tables(self, sql: str) -> List[str]:
        cleaned_sql = re.sub(r"--.*", "", sql)  # Strip line comments
        cleaned_sql = re.sub(
            r"/\*.*?\*/", "", cleaned_sql, flags=re.DOTALL
        )  # Strip block comments

        cte_names = set()
        if re.search(r"^\s*WITH\s+", cleaned_sql, re.IGNORECASE | re.MULTILINE):
            cte_matches = re.findall(
                r"\b(\w+)\s+AS\s*\(", cleaned_sql, re.IGNORECASE | re.DOTALL
            )
            cte_names.update(cte_matches)

        from_tables = re.findall(r"\bFROM\s+([^\s\(\)\,]+)", cleaned_sql, re.IGNORECASE)
        tables = [{"Table": table} for table in from_tables if table not in cte_names]
        return tables


class PythonTool(Tool):
    def __init__(
        self, python_func: callable, tool_description: str, output_description: str
    ) -> None:
        self.python_callable = self.asyncify(python_func)
        self.desc = self._generate_description(
            python_func=python_func,
            tool_description=tool_description,
            output_description=output_description,
        )
        super().__init__(
            name=python_func.__name__, func=self.python_callable, description=self.desc
        )
        gateway_logger.log("INFO", "Python Tool successfully initialized")

    def __call__(self, *args):
        return self.python_callable(*args)

    def asyncify(self, sync_func):
        async def async_func(*args, **kwargs):
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, sync_func, *args, **kwargs)
            return {
                "output": result,
                "sources": {
                    "tool_type": "custom_tool",
                    "tool_name": sync_func.__name__,
                    "metadata": None,
                },
            }

        return async_func

    def _generate_description(
        self, python_func: callable, tool_description: str, output_description: str
    ) -> str:
        full_sig = self._process_full_signature(python_func=python_func)
        return f"""{full_sig}\n - {tool_description}\n - {output_description}"""

    def _process_full_signature(self, python_func: callable) -> str:
        name = python_func.__name__
        signature = str(inspect.signature(python_func))
        return name + signature
