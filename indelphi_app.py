import dash

# external_stylesheets = stylesheet.css
# app = dash.Dash(__name__, external_stylesheets=external_stylesheets)

app = dash.Dash()
server = app.server
app.config.suppress_callback_exceptions = True