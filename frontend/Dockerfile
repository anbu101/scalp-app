FROM node:18-alpine

WORKDIR /app

# 🔥 REQUIRED for macOS Docker hot reload
ENV CHOKIDAR_USEPOLLING=true

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "start"]
