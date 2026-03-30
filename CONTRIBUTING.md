# Contributing to Aletheia

Welcome to **Aletheia**! We're thrilled that you're interested in contributing to the future of AI-native Business Ontologies and Graph Reasoning. Whether you're fixing bugs, adding new graph database connectors, or expanding our Deep Reasoning Agent capabilities, your help is greatly appreciated.

## How to Contribute

### 1. Reporting Bugs
If you find a bug, please open an issue on GitHub. Include:
* A clear and descriptive title.
* Steps to reproduce the issue.
* The expected behavior vs. the actual behavior.
* Logs or stack traces (especially for GraphDB or LLM API errors).

### 2. Suggesting Enhancements
Have an idea for a new Agent, a new data source connector (e.g., MongoDB, Snowflake), or an advanced graph reasoning scenario? Open an issue using the "Enhancement" label. Describe the problem your idea solves and how it fits into the multi-agent architecture.

### 3. Submitting Pull Requests
1. **Fork the Repository:** Create your own fork of `apecloud/Aletheia`.
2. **Create a Branch:** Create a feature branch (`git checkout -b feature/amazing-new-agent`).
3. **Make Changes:** Write your code, ensuring it follows the existing architecture.
4. **Test Your Changes:** Run the pipeline locally (using `./scripts/load_complex_ecommerce_dataset.sh all`) to ensure it doesn't break the existing extraction or reasoning flows. Check that `run_reasoning_result.md` generates without errors.
5. **Commit:** Commit your changes with clear, descriptive commit messages.
6. **Push:** Push your branch to your fork (`git push origin feature/amazing-new-agent`).
7. **Open a PR:** Open a Pull Request against the `main` branch of the upstream repository.

## Development Setup

Please refer to the `README.md` for the full deployment and testing guide. You will generally need:
* Python 3.11+
* MySQL (Source Database for Legacy Data)
* PostgreSQL + PostGIS (Ontology Meta-Graph Storage)
* Nebula Graph (Live Graph Reasoning Storage)
* A valid LLM API Key (e.g., `GEMINI_API_KEY` or `OPENAI_API_KEY`)

## Code Style
* We use standard Python conventions (PEP 8).
* Ensure type hints are used, especially for Pydantic models in the Agent definitions (`litellm` and `instructor` rely on these).
* Provide clear docstrings for new methods, classes, or Agent prompts.

## License
By contributing to Aletheia, you agree that your contributions will be licensed under its Apache-2.0 License.
