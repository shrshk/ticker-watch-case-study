## Albert product engineering case study
Welcome to the Albert product engineering case study. At Albert, we value focused work and shipping product. 
Instead of spending hours interviewing and talking about building things, we would like to give you the opportunity to show us what you can actually build. 
Please approach this case study as if you were working on a real project at Albert with production quality design decisions, architecture, documentation, and code. 

### The project
- Build a stock watchlist service and client application that allows users to view their favorite stock prices.
- Users should be able to search for stocks by name or ticker and add them to a watchlist.
- Users should be able to remove a stock from their watchlist.
- Users should be able to see the current price of the stocks in their watchlist.
- Prices should update every 5 seconds.
- The service should be designed to support millions of users each with their own watchlist.
- User data should be persisted in a database and re-used when the app is restarted.
- The focus for this case study is on the service architecture and communication between the service and client application.
- The client application must support user login and a single screen that contains a search bar, list of stocks and current prices, but it does not need to be a polished UI.

### Setup
#### Stock APIs 
- Albert has a set of REST endpoints to provide stock information for the case study. Treat this API as a 3rd party integration with a per API call pricing model and limit the number of calls you make directly to this API.
- You will need to use the API key provided in the case study email to access the endpoints. Pass the API key in an `Albert-Case-Study-API-Key` header to the REST endpoints.
- The API provides two endpoints, one to get a list of available stock tickers and another to get the current stock prices.

`GET /casestudy/stock/tickers/` 
- Returns a list of the stock tickers and company names that are available for use with the case study.
```commandline
curl "https://app.albert.com/casestudy/stock/tickers/" -H "Albert-Case-Study-API-Key: <token>"

{
    "AAPL": "Apple",
    "GOOG": "Alphabet"
}
```

`GET /casestudy/stock/prices/?tickers=[TICKERS]` 
- Returns the current price for each of the tickets.
```commandline
curl "https://app.albert.com/casestudy/stock/prices/?tickers=AAPL,MSFT" -H "Albert-Case-Study-API-Key: <token>"

{
    "AAPL": 122.21,
    "GOOG": 193.62
}
```

#### Case study service and application scaffolding
- Along with the endpoints for getting stock information we have setup a Django REST Framework service and React application.
- The Django service defines a single `Security` model that represents single stock tickers and a login method with no authentication.
- The React app has a very basic login screen that calls the `/login/` method on the Django service.
- You can extend the Django and React applications to complete the case study or choose to use an entirely separate tech stack.

##### Service files
- `/django/casestudy/migrations/0001_initial.py` - Django initial database migration that creates the Security table.
- `/django/casestudy/admin.py` - Django admin configuration for the Security table.
- `/django/casestudy/models.py` - Django model for the Security table, add any new fields you need to the Security model, or new models if needed. Once you have added new fields run `make migrate` to create the new migration files and apply them to the db.
- `/django/casestudy/settings.py` - Django settings file, with the default settings for the project. You can add any new settings you need to this file.
- `/django/casestudy/urls.py` - Django urls. We have setup the `/admin/` routes for the Django admin and `/login/` route. 
- `/django/casestudy/wsgi.py` - Django wsgi (Web Server Gateway Interface) file, you should not need to touch this file, it is part of the Django framework.

##### Client files
- `/client/src/App.css` - CSS for the React app.
- `/client/src/App.jsx` - React app entry point, with a single `LoginForm`.
- `/client/src/index.css` - CSS for the React index page.
- `/client/src/index.jsx` - React index page.
- `/LoginForm.jxs` - React login form component for loging into the Django `casestudy` service, that calls the `/login/` endpoint.

```commandline
curl 'http://localhost:8000/login/' -H 'Content-Type: application/json' --data '{"username": "user1"}'

{
    "id":2,
    "username":"user1",
    "email":"user1@casestudy.com",
    "first_name":"User",
    "last_name":"One"
}
```

### Getting started
You'll need to run a few `Makefile` commands to get started. Run these commands in single terminal.
- `make build` - this will build the Docker images required to run the Django and Celery services
- `make migrate` - this will create the database tables required by Django
- `make createsuperuser` - this will create a superuser for the Django admin page with username=root and password=root
- `make createusers` - this will create user1 and user1.
- `make up` - this will bring the Django service and React app up.

Run these commands in separate terminal.
- `make open-admin` - this will open the Django admin page in your browser.
- `make open-app` - this will open the React app in your browser.

### Deliverables
- All services must install and run inside Docker.
- A working service and client with source code. 
- `make up` should start the service and client.
- `make open-app` should open the client application.
- Once you have completed the project you can submit your solution by running `make submit` which will package your project into a solution.zip file you can submit through email.
