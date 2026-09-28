FROM mcr.microsoft.com/playwright:v1.58.0-noble
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY src ./src
RUN mkdir -p data logs browser-profile
CMD ["npm","start"]
