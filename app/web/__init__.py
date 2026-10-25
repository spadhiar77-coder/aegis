"""The Aegis web app: server-rendered pages for analysts, built with Jinja2.

Every page is produced by Python on the server from the database and the
model; there is no JavaScript. Forms post back to the server, which applies
the change and redirects (the post/redirect/get pattern), so refreshing a page
never repeats an action.
"""
