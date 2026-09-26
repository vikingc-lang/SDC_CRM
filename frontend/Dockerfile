FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S -g 1001 cirra \&\& adduser -S -u 1001 cirra -G cirra
COPY --from=build --chown=cirra:cirra /app/.next/standalone ./
COPY --from=build --chown=cirra:cirra /app/.next/static ./.next/static
COPY --from=build --chown=cirra:cirra /app/public ./public
USER cirra
EXPOSE 3000
CMD ["node", "server.js"]
