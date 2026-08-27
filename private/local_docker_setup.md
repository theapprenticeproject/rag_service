# Local Docker setup for rag_service

This guide creates a fresh local Frappe site named `rag_service.localhost` using Docker, Postgres, Redis, and the local `rag_service` app source from this repository.

The recommended path is the root stack script:

```sh
cd /Users/TAP/Documents/Git/LMS
cp .env.example env.local
./scripts/start_local_docker.sh
```

## External RabbitMQ

RabbitMQ is created externally in CloudAMQP:

```text
https://customer.cloudamqp.com/login
```

Fill these root `env.local` values from the CloudAMQP instance:

```dotenv
RABBITMQ_HOST=
RABBITMQ_PORT=5672
RABBITMQ_VIRTUAL_HOST=
RABBITMQ_USERNAME=
RABBITMQ_PASSWORD=
RABBITMQ_PLAGIARISM_RESULTS_QUEUE=plagiarism_feedback
RABBITMQ_FEEDBACK_RESULTS_QUEUE=feedback_results
```

## Required local DocTypes discovered in code

The active folders reviewed were:

- `rag_service/core`
- `rag_service/feedback_utils`
- `rag_service/utils`
- `rag_service/rag_service/doctype`

The local setup needs these settings available:

- `RabbitMQ Settings`: used by `utils/rabbitmq_consumer.py` and `utils/queue_manager.py`.
- `GCS Settings`: used by `utils/gcp_service_client.py`.
- `RAG Settings`: used by `core/assignment_context_manager.py` to fetch assignment and student context from CRM.
- `LLM Settings`: used by most feedback generation paths.
- `Gemini Settings`: used by Gemini-specific image evaluation paths.
- `Prompt Segment` and `Prompt Template`: required by template-driven feedback generation.

The root script symlinks `/home/frappe/frappe-bench/apps/rag_service` to `/workspace/rag_service`, installs `rag_service` in editable mode, installs `business_theme_v14`, and seeds `RabbitMQ Settings`, `GCS Settings`, and `RAG Settings` from `env.local`. It also creates optional `LLM Settings` and `Gemini Settings` rows when the related env values are present.

## Values to set before use

In root `env.local`, set:

```dotenv
RAG_TAP_LMS_BASE_URL=http://tap_lms.localhost:8000
RAG_ASSIGNMENT_CONTEXT_ENDPOINT=/api/method/tap_lms.api.get_assignment_context
RAG_STUDENT_CONTEXT_ENDPOINT=/api/method/tap_lms.api.get_student_context
RAG_TAP_LMS_API_KEY=
RAG_TAP_LMS_API_SECRET=
RAG_ENABLE_CACHING=1
RAG_CACHE_DURATION_DAYS=1
```

For model-backed feedback generation, also set one of:

```dotenv
RAG_LLM_PROVIDER=Gemini
RAG_LLM_MODEL_NAME=
RAG_LLM_API_KEY=
RAG_LLM_API_SECRET=
```

or:

```dotenv
RAG_GEMINI_MODEL_NAME=
RAG_GEMINI_PROJECT_ID=
RAG_GEMINI_LOCATION=us-central1
RAG_GEMINI_CREDENTIALS_JSON={}
```

## Start RAG after setup

```sh
docker compose --env-file env.local -f docker/local/docker-compose.yml exec rag-dev bash -lc "cd /home/frappe/frappe-bench && bench start"
```

Open:

```text
http://rag_service.localhost:8001
```

Login:

```text
User: Administrator
Password: the RAG_ADMIN_PASSWORD value from `env.local`
```

## Consumer

The app exposes a Frappe command for the RabbitMQ consumer. After the site is installed and RabbitMQ settings are valid, run it from the RAG dev container:

```sh
docker compose --env-file env.local -f docker/local/docker-compose.yml exec rag-dev bash -lc "cd /home/frappe/frappe-bench && bench --site rag_service.localhost start-rag-consumer"
```

If the command path changes, inspect `rag_service/hooks.py` and `rag_service/commands`.
