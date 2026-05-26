# llm-d-skills for accelerated task automation and standardized workflows

**Authors**: Rachel Brill (_IBM_), Sharon Keidar-Barner (_IBM_), Michal Malka (_IBM_)

## Summary

[Skills](https://github.com/anthropics/skills) are an increasingly popular concept in agent-based code development. They are dynamically loaded by code assistants and encapsulate instructions and guidelines for performing specific, repeatable tasks, effectively replacing human effort. Unlike traditional automation that replaces human effort with deterministic code, skills rely on large language models (LLMs) and apply reasoning when tasks are ill-defined or require special adaptation.

Skills seem a natural fit to accelerate and automate commonly repeated tasks by llm-d developers and users, such as deploying stacks with different configurations, running benchmarking against stacks with varying workloads, and enabling different features in running stacks. As a proof-of-concept, we have created an initial set of skills for (1) deploying an llm-d stack, (2) running benchmarks, (3) tearing down stacks, and (4) comparing benchmark results across two llm-d configurations (a new feature). The implemented skills leverage existing code and documentation as much as possible. The initial feedback we received is that the tasks are much more automatic and easier to perform using the skills, and that the execution of the skills is also easy to extend and customize to fit specific use cases.

## Motivation

Skills run by code assistants accelerate task automation and standardize workflows. They can address a wide range of scenarios across different environments, and they are designed to work with any code assistant. Creating a central location for llm-d skills enables (1) easy sharing and reuse, (2) distribution of approved workflows consistently across the community, and (3) ensuring standardized procedures and best practices are followed. Skills also assist in onboarding of new members by providing them with a set of pre-defined skills that can be used to accelerate their work. This will help them get up to speed faster and contribute more effectively to the community.

## Proposal

We propose opening a dedicated skills repository for llm-d to accelerate development of workflows to be carried out with code assistants. The repository will be populated with the initial set of skills already created, and new skills will be added incrementally. Each llm-d repository will be able to import skills aligned with its specific scope and goals.

As a reference point, other open-source projects have already established skills repositories to accelerate development. For example, vLLM maintains a [skills repository](https://github.com/vllm-project/vllm-skills/blob/main/skills/vllm-prefix-cache-bench/SKILLS.md) that includes skills for basic and Docker-based deployment, as well as benchmarking automatic prefix caching.
