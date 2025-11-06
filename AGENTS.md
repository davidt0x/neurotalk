# Run

We are developing using VSCode in WSL. We are using uv for virtual environments. In general, use UV_PROJECT_ENVIRONMENT="/home/david/.virtualenvs/neurotalk/"
However, since psychopy and pygame (used by experiments in examples/) are windows only dependencies, please use the windows uv venv under 
/c/Users/david/.virtualenvs/neurotalk. This can be invoked with uv under powershell. Only do this if the psychopy or pygame dependcies are needed for the 
scripts you are testing.