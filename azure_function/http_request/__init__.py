import logging

import azure.functions as func

from jinja2 import Template

import http.client, urllib.request, urllib.parse, urllib.error, base64, json

from bokeh.io import output_file, show
from bokeh.models import (BoxZoomTool, Circle, HoverTool,
                          MultiLine, Plot, Range1d,
                          ResetTool, WheelZoomTool,
                          TapTool, OpenURL, ColumnDataSource, HelpTool, Label)
from bokeh.models.widgets.markups import Div
from bokeh.layouts import Column
from bokeh.palettes import Spectral4
from bokeh.plotting import from_networkx, figure
from bokeh.models.graphs import NodesAndLinkedEdges
from bokeh.embed import components
from bokeh.models.callbacks import CustomJS
from bokeh.palettes import OrRd9, Blues9

import networkx as nx

import numpy as np


t = Template("""
<html style="height:100vh;">
    <head>
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-2.0.2.min.js"
            crossorigin="anonymous"></script>
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-widgets-2.0.2.min.js"
            crossorigin="anonymous"></script>
        <script src="https://cdn.bokeh.org/bokeh/release/bokeh-tables-2.0.2.min.js"
            crossorigin="anonymous"></script>
    </head>
    <body style="height:100vh; font-family: Helvetica, sans-serif;">
        <div style="height: 100%">
            <H1>Science Graph. <a href="https://github.com/marcus-o/sciencegraph/">Code on Github.</a></H1>
            <form action="/api/http_request">
                Search the <a href="https://aka.ms/msracad">Microsoft Academic Graph</a>:
                <input name="query", value="{{ query }}"/>
                <select name="n">
                    {% for o in select_options %}
                        {% if o.value == so %}
                            <option selected value="{{ o.value }}">{{ o.label }}</option>
                        {% else %}
                            <option value="{{ o.value }}">{{ o.label }}</option>
                        {% endif %}
                    {% endfor %}
                </select>
                <input type="submit">
            </form>
            {{ script|safe }}
            <div style="height: 70%">
                {{ div|safe }}
            </div>
        </div>
    </body>
</html>
""")

tooltips = """
    <div style="max-width : 300px">
            <div><span style="">@type</span></div>
            <div><span style="font-weight: bold;">@title</span></div>
            <div><span style="">@authors</span></div>
            <div><span style="font-weight: bold;">@journal</span></div>
            <div><span style="font-weight: bold;">@year</span></div>
            <div><span style=""><a href="https://doi.org/@DOI">@DOI</a></span></div>
    </div>
"""

showpaper_content = """
    <div><span style="">@type</span></div>
    <div><span style="font-weight: bold;">@title</span></div>
    <div><span style="">@authors</span></div>
    <div><span style="font-weight: bold;">@journal</span></div>
    <div><span style="font-weight: bold;">@year</span></div>
    <div><span style=""><a target="_blank" href="https://doi.org/@DOI">@DOI</a></span></div>
"""

code = """
    if (cb_data.source.selected.indices.length > 0){
        var selected_index = cb_data.source.selected.indices[0];
        var tooltip = document.getElementById('showpaper');
        cb_data.source.data.color[selected_index] = 'grey';

        tooltip.style.display = 'block';
        tooltip.style.left = '5px';
        tooltip.style.top = '5px';
        tooltip.style.width = '500px';

        tp = tp.replace('@type', cb_data.source.data.type[selected_index]);
        tp = tp.replace('@title', cb_data.source.data.title[selected_index]);
        tp = tp.replace('@authors', cb_data.source.data.authors[selected_index]);
        tp = tp.replace('@journal', cb_data.source.data.journal[selected_index]);
        tp = tp.replace('@year', cb_data.source.data.year[selected_index]);
        tp = tp.replace('@DOI', cb_data.source.data.DOI[selected_index]);
        tp = tp.replace('@DOI', cb_data.source.data.DOI[selected_index]);
        tooltip.innerHTML = tp;
    }"""

# %%
cm1 = Blues9
cm2 = OrRd9

headers = {
    # Request headers
    'Content-Type': 'application/x-www-form-urlencoded',
    'Ocp-Apim-Subscription-Key': '',
}


# microsoft academic graph requests
class ResponseError(Exception):
    def __init__(self, errno, strerror):
        self.errno = errno
        self.strerror = strerror


def interpret(query):
    params = urllib.parse.urlencode({
        'model': 'latest',
        'count': '100',
        'offset': '0',
        'query': query,
    })

    try:
        conn = http.client.HTTPSConnection('api.labs.cognitive.microsoft.com')
        conn.request("POST", "/academic/v1.0/interpret", params, headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        data_decoded = json.loads(data)
        if 'Error' in data_decoded.keys():
            raise ResponseError(
                -100, 'got answer but error: ' + str(data_decoded))
        return(data_decoded)
    except Exception as e:
        print("[Errno {0}] {1}".format(e.errno, e.strerror))
        return(None)


def evaluate(query, n=100):
    params = urllib.parse.urlencode({
        # Request parameters
        'model': 'latest',
        'count': n,
        'offset': '0',
        'orderby': '',
        'attributes': 'Id,DN,Y,CC,J.JN,AA.AuId,AA.DAuN,AA.DAfN,RId,DOI',
        'expr': query,
    })

    try:
        conn = http.client.HTTPSConnection('api.labs.cognitive.microsoft.com')
        conn.request("POST", "/academic/v1.0/evaluate", params, headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        data_decoded = json.loads(data)
        if 'Error' in data_decoded.keys():
            raise ResponseError(
                -100, 'got answer but error: ' + str(data_decoded))
        return(data_decoded)
    except Exception as e:
        print("[Errno {0}] {1}".format(e.errno, e.strerror))
        return(None)


def prepare_data(query, n):
    # %% convert the natural language request to a query
    interpret_data = interpret(query)
    # %% get the most likely query result
    if 'interpretations' not in interpret_data.keys():
        return 0, 0
    exprs = [
        e['rules'][0]['output']['value']
        for e in interpret_data['interpretations']
        if e['rules'][0]['output']['type'] == 'query']
    eval_data = evaluate(exprs[0], n=n)
    if 'entities' not in eval_data.keys():
        return 0, 0
    # %% process primary found papers
    papers = [e for e in eval_data['entities']]
    # strip incomplete
    papers = [p for p in papers if 'DN' in p.keys()]
    papers = [p for p in papers if 'AA' in p.keys()]
    # papers = [p for p in papers if 'DAuN' in p['AA'].keys()]
    papers = [p for p in papers if 'J' in p.keys()]
    # papers = [p for p in papers if 'JN' in p['JN'].keys()]
    papers = [p for p in papers if 'Y' in p.keys()]
    papers = [p for p in papers if 'CC' in p.keys()]
    for p in papers:
        if 'DOI' not in p.keys():
            p['DOI'] = 'unknown'
    max_cit = max([p['CC'] for p in papers])
    # create parallel array as this is the main identifier,
    # should make sure to retain ordering stays aligned along the script
    ids = [p['Id'] for p in papers]
    # extract all references
    rids = []
    _ = [
        rids.extend(ridsl)
        for ridsl in [
            p['RId']
            for p in papers
            if 'RId' in p.keys()]]
    # get rid of reference ids that are already in the primary request
    rids = [rid for rid in rids if rid not in ids]

    # %% get the secondary found papers information
    expr_ref = "Or(Id=" + ",Id=".join([str(rdi) for rdi in rids]) + ")"
    eval_data_ref = evaluate(expr_ref, n=1000)
    if 'entities' in eval_data.keys():
        # %% process secondary found papers
        papers_ref = [e for e in eval_data_ref['entities']]

        # strip incomplete
        papers_ref = [p for p in papers_ref if 'DN' in p.keys()]
        papers_ref = [p for p in papers_ref if 'AA' in p.keys()]
        # papers = [p for p in papers if 'DAuN' in p['AA'].keys()]
        papers_ref = [p for p in papers_ref if 'J' in p.keys()]
        # papers = [p for p in papers if 'JN' in p['JN'].keys()]
        papers_ref = [p for p in papers_ref if 'Y' in p.keys()]
        papers_ref = [p for p in papers_ref if 'CC' in p.keys()]
        for p in papers_ref:
            if 'DOI' not in p.keys():
                p['DOI'] = 'unknown'
        max_cit_ref = max([p['CC'] for p in papers_ref])
        ids_ref = [p['Id'] for p in papers_ref]

        # rids_ref = []
        # _ = [
        # rids_ref.extend(ridsl)
        # for ridsl in [
        #   e['RId']
        #   for e in papers_ref
        #   if 'RId' in e.keys()]]
        # rids_ref = [rid for rid in rids_ref if rid not in ids]
    else:
        # print('no refdata')
        papers_ref = []
        ids_ref = []
        max_cit_ref = 0

    # max_cit = max([max_cit, max_cit_ref])
    # %%
    G = nx.Graph()
    # add primary papers
    for id, paper in zip(ids, papers):
        color = cm2[int(8*(1-paper['CC']/max_cit))]
        G.add_node(
            id,
            type='Primary Search Result',
            color=color,
            title=paper['DN'],
            authors=', '.join([a['DAuN'] for a in paper['AA']]),
            journal=paper['J']['JN'],
            year=paper['Y'],
            DOI=paper['DOI'],
            size=20)
    # add their references
    for id, paper in zip(ids_ref, papers_ref):
        color = cm1[int(8*(1-paper['CC']/max_cit_ref))]
        G.add_node(
            id,
            type='Reference',
            color=color,
            title=paper['DN'],
            authors=', '.join([a['DAuN'] for a in paper['AA']]),
            journal=paper['J']['JN'],
            year=paper['Y'],
            DOI=paper['DOI'],
            size=10)

    # add connections from primaries to references
    for p in papers:
        if 'RId' in p.keys():
            # between primaries
            G.add_edges_from(
                [(p['Id'], rid) for rid in p['RId'] if rid in ids])
            # between primaries and references
            G.add_edges_from(
                [(p['Id'], rid) for rid in p['RId'] if rid in ids_ref])
    # add connections between references
    for p in papers_ref:
        if 'RId' in p.keys():
            G.add_edges_from(
                [(p['Id'], rid) for rid in p['RId'] if rid in ids_ref])
    return G, exprs[0]


def prepare_data_authors(query):
    # %% convert the natural language request to a query
    interpret_data = interpret(query)
    exprs = [
        e['rules'][0]['output']['value']
        for e in interpret_data['interpretations']
        if e['rules'][0]['output']['type'] == 'query']

    # for expr in exprs:
    #    if 'AA.AuN=' in expr:
    #        res_expr = expr
    # else:
    #    res_expr = exprs[0]

    # %%
    eval_data = evaluate(exprs[0], n=1000)
    if 'entities' not in eval_data.keys():
        return 0, 0
    # %% process primary found papers
    papers = [e for e in eval_data['entities']]
    # strip incomplete
    papers = [p for p in papers if 'DN' in p.keys()]
    papers = [p for p in papers if 'AA' in p.keys()]
    papers = [p for p in papers if 'J' in p.keys()]
    papers = [p for p in papers if 'Y' in p.keys()]
    papers = [p for p in papers if 'CC' in p.keys()]
    for p in papers:
        if 'DOI' not in p.keys():
            p['DOI'] = 'unknown'
    max_cit = max([p['CC'] for p in papers])
    ids = [p['Id'] for p in papers]

    authors = []
    [
        authors.extend(a)
        for a in [
            p['AA'] for p in papers]]
    auids = [a['AuId'] for a in authors]
    auids, auids_idx, auids_counts = np.unique(
        auids, return_index=True, return_counts=True)
    auids = [int(auid) for auid in auids]
    authors = [authors[idx] for idx in auids_idx]

    G = nx.Graph()
    # add primary papers
    for id, paper in zip(ids, papers):
        color = cm2[int(8*(1-paper['CC']/max_cit))]

        G.add_node(
            id,
            type='Publication',
            color=color,
            title=paper['DN'],
            authors=', '.join([a['DAuN'] for a in paper['AA']]),
            journal=paper['J']['JN'],
            year=paper['Y'],
            DOI=paper['DOI'],
            size=15)
    # add co-auhors
    second_max = sorted(auids_counts)[-2]
    for auid, author, occ in zip(auids, authors, auids_counts):
        if max(auids_counts) == occ:
            size = 20
            color = cm1[0]
        else:
            size = 10
            color = cm1[int(8*(1-occ/second_max))]
        G.add_node(
            auid,
            type='Author',
            color=color,
            title=author['DAuN'],
            authors=author['DAfN'],
            journal='',
            year='',
            DOI='',
            size=size)

    # add connections from primaries to references
    for p in papers:
        if 'AA' in p.keys():
            G.add_edges_from([(p['Id'], a['AuId']) for a in p['AA']])
    return G, exprs[0]


def draw_plot(G, query, expr, type='publications'):
    # plot
    plot = figure(
        x_range=Range1d(-1.1, 1.1), y_range=Range1d(-1.1, 1.1),
        sizing_mode="stretch_both",
        tools="")
    plot.axis.visible = False
    plot.xgrid.grid_line_color = None
    plot.ygrid.grid_line_color = None

    # legend
    plot.circle(
        x=[-200000, ], y=[-200000, ],
        fill_color='white', size=0, line_width=0,
        legend_label='Visualization for "' + query + '"')
    plot.circle(
        x=[-200000, ], y=[-200000, ],
        fill_color='white', size=0, line_width=0,
        legend_label='created using Microsoft Academic Graph and')
    plot.circle(
        x=[-200000, ], y=[-200000, ],
        fill_color='white', size=0, line_width=0,
        legend_label='Sciencegraph by Marcus Ossiander, 2020')

    if type == 'publications':
        plot.circle(
            x=[-200000, ], y=[-200000, ],
            fill_color=cm2[3], size=20,
            legend_label='Publication, Color measures Citation Count')
        plot.circle(
            x=[-200000, ], y=[-200000, ],
            fill_color=cm1[3], size=10,
            legend_label='Reference, Color measures Citation Count')
    if type == 'authors':
        plot.circle(
            x=[-200000, ], y=[-200000, ],
            fill_color=cm2[3], size=15,
            legend_label='Publication, Color measures Citation Count')
        plot.circle(
            x=[-200000, ], y=[-200000, ],
            fill_color=cm1[3], size=10,
            legend_label='Co-Author, Color measures Collaboration')
    plot.legend.background_fill_alpha = 0
    plot.legend.border_line_alpha = 0
    plot.legend.location = 'top_left'

    # tools
    node_hover_tool = HoverTool(tooltips=tooltips)
    zoom_tool = WheelZoomTool()

    div = Div(
        text='<div id="showpaper" style="position: absolute; display: none; width=500px"></div>',
        name='showpaper', sizing_mode="stretch_width")
    tap_tool_open = TapTool()
    tap_tool_open.callback = CustomJS(
        args={'tp': showpaper_content}, code=code)
    help_tool = HelpTool(
        help_tooltip='Created using Microsoft Academic Graph and Sciencegraph by Marcus Ossiander, 2020',
        redirect='https://github.com/marcus-o/sciencegraph/')
    plot.add_tools(
        node_hover_tool,
        zoom_tool,
        BoxZoomTool(),
        ResetTool(),
        tap_tool_open,
        help_tool)
    plot.toolbar.active_scroll = zoom_tool

    # graph
    graph_renderer = from_networkx(
        G, nx.spring_layout, scale=1, center=(0, 0), seed=12345)
    # normal
    graph_renderer.node_renderer.glyph = Circle(
        size="size", fill_color="color")
    graph_renderer.edge_renderer.glyph = MultiLine(
        line_alpha=0.2)
    # selection
    graph_renderer.node_renderer.selection_glyph = Circle(
        fill_color="color", fill_alpha=1, line_alpha=1)
    graph_renderer.edge_renderer.selection_glyph = MultiLine(
        line_width=3, line_alpha=1)
    graph_renderer.node_renderer.nonselection_glyph = Circle(
        fill_color="color", fill_alpha=0.5, line_alpha=0.5)
    graph_renderer.edge_renderer.nonselection_glyph = MultiLine(
        line_alpha=0.2)
    # hover
    graph_renderer.node_renderer.hover_glyph = Circle(
        fill_color='#abdda4')
    graph_renderer.edge_renderer.hover_glyph = MultiLine(
        line_color='#abdda4', line_width=3)
    graph_renderer.inspection_policy = NodesAndLinkedEdges()
    graph_renderer.selection_policy = NodesAndLinkedEdges()

    # add everything
    plot.renderers.append(graph_renderer)
    script, div = components(Column(children=[plot, div], sizing_mode="stretch_both"))
    return script, div


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        logging.info('Python HTTP trigger function processed a request.')

        select_options = [
            {'value': '10', 'label': 'Pub. and Ref., n=10'},
            {'value': '20', 'label': 'Pub. and Ref., n=20'},
            {'value': '50', 'label': 'Pub. and Ref., n=50'},
            {'value': 'A', 'label': 'Co-Authors'}]

        query = req.params.get('query')
        n = req.params.get('n')

        try:
            if not n:
                n = '20'
            n = int(n)
            if n > 100:
                n = 100
            if n < 1:
                n = 1
        except Exception:
            n = 'A'

        if not(n == 'A'):
            if not query:
                query = 'metasurface'

            graph, expr = prepare_data(query, n=n)
            plot_script, plot_div = draw_plot(
                graph, query, expr, type='publications')
            return func.HttpResponse(t.render(
                script=plot_script,
                div=plot_div,
                query=query,
                select_options=select_options,
                so=str(n)), headers={'content-type': 'text/html'})
        else:
            if not query:
                query = 'federico capasso'

            graph, expr = prepare_data_authors(query)
            plot_script, plot_div = draw_plot(
                graph, query, expr, type='authors')
            return func.HttpResponse(t.render(
                script=plot_script,
                div=plot_div,
                query=query,
                select_options=select_options,
                so=str(n)), headers={'content-type': 'text/html'})
    except Exception:
        print(Exception)
        return func.HttpResponse(
            'something went south', headers={'content-type': 'text/html'})