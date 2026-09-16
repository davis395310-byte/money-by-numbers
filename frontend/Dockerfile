FROM node:24-alpine AS build

WORKDIR /srv/app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

FROM node:24-alpine

WORKDIR /srv/app

COPY --from=build /srv/app ./

EXPOSE 3000

CMD ["npm", "start"]
