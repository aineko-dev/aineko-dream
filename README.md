# aineko-dream

An example pipeline

## Tutorial

See a tutorial [here](https://docs.aineko.dev/dev/examples/aineko-dream/)

## Setup development environment

```
poetry install
```

## Running the pipeline

First, make sure that docker is running and run the required docker services in the background

```
poetry run aineko service start
```

Then start the pipeline using
```
poetry run aineko run conf/pipeline.yml
```

## Observe the pipeline

To view the data flowing in the datasets

```
poetry run aineko stream logging
```

To view all data in the dataset, from the start

```
poetry run aineko stream logging -b
```


## Taking down a pipeline

In the terminal screen running the pipeline, you can press `ctrl-c` to stop execution.

Clean up background services
```
poetry run aineko service stop
```


## Try it out!

Visit http://localhost:8000/docs to view the API documentation via Swagger. Use the `test` endpoint to give it a spin.
