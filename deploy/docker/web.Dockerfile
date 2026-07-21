FROM node:22-alpine AS dependencies
WORKDIR /app
COPY studio/web/package.json studio/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_TRACEBISECT_API_URL=""
ENV NEXT_PUBLIC_TRACEBISECT_API_URL=${NEXT_PUBLIC_TRACEBISECT_API_URL}
COPY --from=dependencies /app/node_modules ./node_modules
COPY studio/web ./
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY studio/web/package.json studio/web/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && chown -R node:node /app
COPY --from=builder --chown=node:node /app/.next ./.next

USER node

EXPOSE 3000

CMD ["npm", "run", "start"]
