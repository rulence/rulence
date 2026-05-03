FROM node:22-alpine

WORKDIR /app
RUN apk add --no-cache python3
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src-js ./src-js
COPY src ./src
COPY examples ./examples

ENV NODE_ENV=production
ENV PYTHONPATH=/app/src
CMD ["node", "./src-js/mcp-server.js"]
