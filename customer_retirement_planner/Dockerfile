# Frontend image for the AI-Based Customer Retirement Planner (React SPA).
#
# Multi-stage: build the CRA production bundle with Node, then serve the static
# assets with nginx. REACT_APP_API_URL is baked in at build time (CRA inlines
# env vars prefixed with REACT_APP_ during `npm run build`).
#
# Build: docker build --build-arg REACT_APP_API_URL=http://localhost:5000 \
#          -t crp-frontend ./customer_retirement_planner

# ---- Stage 1: build ----
FROM node:20-alpine AS build

WORKDIR /app

# Install deps against the lockfile for reproducible builds.
COPY package.json package-lock.json ./
RUN npm ci

# API base URL is a build-time arg (CRA inlines REACT_APP_* at build).
ARG REACT_APP_API_URL=http://localhost:5000
ENV REACT_APP_API_URL=$REACT_APP_API_URL

COPY . .
RUN npm run build

# ---- Stage 2: serve ----
FROM nginx:1.27-alpine AS serve

# SPA-aware nginx config (client-side routing falls back to index.html).
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static assets from the build stage.
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
