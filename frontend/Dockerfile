# Demo UI. Built to static files and served by nginx.
#
# Vite inlines VITE_API_BASE_URL at BUILD time, so the API URL is a build arg
# rather than a runtime env var. In AWS the same bundle is built by CI with the
# ALB URL and uploaded to S3/CloudFront; this image exists for local compose.

ARG NODE_VERSION=22.22.1

FROM node:${NODE_VERSION}-slim AS build

WORKDIR /build

# Lockfile first, so dependency installs cache independently of source changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.29-alpine AS runtime

COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
