FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY nql ./nql
RUN pip install --no-cache-dir .

# Inside a container we must listen on all interfaces; publish the port to
# 127.0.0.1 only (see README) so it is still reachable from this machine alone.
EXPOSE 8765
CMD ["nql", "--host", "0.0.0.0", "--no-browser"]
