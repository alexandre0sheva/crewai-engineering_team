# The image the checks of Node/TypeScript benchmark tasks run in (no network at check time).
#   docker build -t engineering-team-bench-node:1 benchmarks/images/node
# The packages live in /opt/bench/node_modules; a task's workspace is mounted at /opt/bench/app,
# so Node's module resolution finds them one directory up. Node 24 runs TypeScript files directly
# (type stripping), so no compiler is installed.
FROM node:24-slim
WORKDIR /opt/bench
COPY package.json ./
RUN npm install --no-audit --no-fund --ignore-scripts && npm cache clean --force
WORKDIR /opt/bench/app
