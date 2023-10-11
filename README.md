# Aineko Dream

Generative templating for the Aineko framework.

## Tutorial

See a tutorial here: https://docs.aineko.dev/examples/aineko-dream-code-generation-using-chatgpt-with-real-time-qa

## Setup development environment

```
poetry install
```

## Running the pipeline

First, make sure that docker is running and run the required docker services in the background

```
aineko service start
```

Then start the pipeline using
```
aineko run -c conf/gpt3.yml
```

## Observe the pipeline

To view the data flowing in the datasets

```
aineko stream --dataset user_prompt
```

To view all data in the dataset, from the start

```
aineko stream --dataset user_prompt --from-start
```

## Taking down a pipeline

In the terminal screen running the pipeline, you can press `ctrl-c` to stop execution.

Clean up background services
```
aineko service stop
```
