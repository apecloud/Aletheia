# Aletheia (αλήθεια)

> **"Unveiling the Semantic Truth Hidden Within Legacy Data."**

**Aletheia** is an AI-native, Multi-Agent orchestration framework designed to automate the transformation of traditional Relational Databases (RDBMS) into rich, object-centric **Business Ontologies**. 

By leveraging the "unconcealment" philosophy, Aletheia bridges the gap between fragmented legacy data silos and the deterministic, semantic reasoning required by next-generation AI Agents.

---

## 🌟 The Vision

Traditional databases are fossils of business logic—structured for storage, not for understanding. **Aletheia** acts as a "Digital Archeologist," using a collaborative swarm of specialized LLM agents to:
1.  **Extract** latent business meaning from cryptic schemas.
2.  **Reconstruct** data into real-world Objects and Links.
3.  **Enable** safe, closed-loop Actions for AI-driven decision making.

---

## 🏗 Multi-Agent Architecture

Aletheia operates through a decentralized group of specialized agents, each focused on a specific layer of the transformation lifecycle:

### 1. Knowledge Extraction Group (The Archeologists)
* **Metadata Scraper Agent**: Scans DDLs, constraints, and comments to build the physical foundation.
* **Data Profiler Agent**: Analyzes data distributions and samples to validate semantic hypotheses.
* **Business Context Agent**: Ingests external documentation (PDFs, APIs) to align technical tables with business terminology.

### 2. Design & Modeling Group (The Architects)
* **Object Modeler Agent**: Collapses normalized tables into cohesive, high-level Business Objects.
* **Link Weaver Agent**: Discovers explicit and implicit relationships (Links) across the enterprise graph.
* **Action Synthesizer Agent**: Maps stored procedures and triggers to executable, safe business Actions.

### 3. Validation & Testing Group (The Guardians)
* **Semantic Consistency Agent**: Ensures the generated ontology is logically sound and free of contradictions.
* **Ontology Reasoning Agent**: Performs deep, multi-hop graph reasoning to solve complex business queries directly on the semantic map.

---

## 🚀 Key Features

* **Zero-ETL Transition**: Create a semantic "Digital Twin" overlay without moving your data.
* **LLM-Ready Tooling**: Automatically exports the ontology into a format compatible with Palantir AIP-style tool-calling for LLMs.
* **Actionable Governance**: Encapsulates database writes into audited "Actions," preventing AI hallucinations from corrupting production systems.
* **PostGIS Ontology Storage**: Stores the semantic map natively in PostgreSQL with PostGIS capabilities.

---

## 🛠 Deployment and Testing Guide

The Aletheia project is implemented as a pipeline of Python-based LLM Agents (powered by `litellm` and `instructor`), prioritizing the **Gemini 3.1 Pro Preview** model.

### 1. Prerequisites
* Python 3.11+
* **MySQL**: Source database for legacy/raw data (`aletheia_test_data` at `127.0.0.1:3306`).
* **PostgreSQL + PostGIS**: Target database for the semantic ontology (`aletheia_ontology` at `127.0.0.1:5432`).
* **API Key**: Export your Gemini API Key:
  ```bash
  export GEMINI_API_KEY="your-api-key"
  ```

### 2. Environment Setup
Clone the repository and install the dependencies:
```bash
git clone https://github.com/your-username/aletheia.git
cd aletheia
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Ensure your database users (`aletheia_user` for MySQL, `aletheia_pg_user` for PostgreSQL) and schemas are created and accessible.

### 3. Step-by-Step Execution Pipeline

Aletheia provides a set of shell scripts in the `scripts/` directory to run the agents in the correct sequence.

#### Phase 0: Load Test Data
Load a complex, normalized E-commerce dataset (TPC-H style/Northwind) into the raw MySQL database to act as our legacy system.
```bash
./scripts/load_complex_ecommerce_dataset.sh
```

#### Phase 1: Knowledge Extraction
Extract the physical schema and align it with external business context.
```bash
# 1. Scrape raw metadata (tables, columns, types) into PostGIS
./scripts/run_metadata_scraper.sh

# 2. Profile the data to infer semantic types
./scripts/run_data_profiler.sh

# 3. Align technical tables with human-readable business context 
# (Reads from ./docs/ if external documentation exists)
./scripts/run_business_context.sh
```

#### Phase 2: Design & Modeling
Let the AI Architects reconstruct the fragmented tables into a cohesive business graph and extract executable actions.
```bash
# 1. Collapse tables into Business Objects and discover Links
./scripts/run_design_modeling.sh

# 2. Extract and sanitize stored procedures/triggers into Business Actions
./scripts/run_action_synthesizer.sh
```

#### Phase 3: Graph Ingestion
Load the generated objects and links into Nebula Graph to establish the true Business Ontology. This step finalizes the knowledge graph, enabling deterministic reasoning.
```bash
# Ingest only Relationship Edges (Phase 2)
./scripts/run_graph_ingestion.sh 2

# Ingest both Object Nodes and Relationship Edges (Phase All)
./scripts/run_graph_ingestion.sh all
```

#### Phase 4: Semantic Validation
Ensure the generated ontology is logically sound and free of contradictions.
```bash
# Check for logical contradictions, missing links, or orphaned objects
./scripts/run_semantic_consistency.sh
```

#### Phase 5: Deep Ontology Graph Reasoning
Leverage the complete ontology network (Metadata + Semantic Profiles) alongside **LIVE data dynamically fetched** from Nebula Graph to perform complex multi-hop business deductions.
```bash
# Execute analytical Graph Reasoning cases on real Subgraphs (e.g. dynamic LTV calculation, Supply Chain Risk)
./scripts/run_ontology_reasoning.sh
```

### 4. Inspecting the Ontology & Reasoning Results
Once the pipeline is complete, you can review the profound business insights generated by the Graph Reasoning Agent. The dynamic runtime outputs are saved to `run_reasoning_result.md`.

You can also check out our pre-computed [EXAMPLE_REASONING_RESULT.md](./EXAMPLE_REASONING_RESULT.md) on GitHub to see the depth of analysis Aletheia provides.

```bash
# View the Deep Ontology Graph Reasoning Results from your current run
cat run_reasoning_result.md
```

You can also use the unified query tool to explore the AI-generated Semantic Map stored in PostGIS.

```bash
# View everything (Tables, Columns, Objects, Links, Actions)
.venv/bin/python query_metadata.py --all
```

---

## 🤝 Contributing
We welcome contributions to Aletheia! Whether it's adding a new database connector or improving the reasoning logic of our agents, please check out our `CONTRIBUTING.md`.

## 📄 License
This project is licensed under the Apache 2.0 License - see the `LICENSE` file for details.
