# Docs for v1 can be found by changing the above selector ^
from together import Together
import os

client = Together(
    api_key="key",
)
model_name = "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
models = client.models.list()

for model in models:
    if model.id == model_name:
        input_cost = model.pricing.input
        output_cost = model.pricing.output
        print(f"Model: {model.id}")
        print(f"Input Cost per Token: ${input_cost}")
        print(f"Output Cost per Token: ${output_cost}")
        break