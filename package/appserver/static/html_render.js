require([
    'splunkjs/mvc/tableview',
    'splunkjs/mvc',
    'underscore'
],function(
    TableView,
    mvc,
    _
){

    var tokens = mvc.Components.get('submitted');

    var HTMLRenderer = TableView.BaseCellRenderer.extend({
        canRender: function(cell) {
            //Provide the name of the table cell that contains the HTML you want to render
            return _(['Details']).contains(cell.field);
        },
        render: function($td, cell) {
            var value = cell.value;

            //Render HTML of cell's value
            $td.html(value);

        }
    });

    var overview_table = mvc.Components.get("overview_table");

    overview_table.on("click", function(e) {

        console.log("table data: ", e);
        var description = e.data['row.description'];
        var title = "<h3>" + e.data['row.Name'] + "</h3>";
        description = title + description;
        $(document).find("#ta_description").html(description);

    });

    var advisor_details = mvc.Components.getInstance("advisor_details");

    advisor_details.getVisualization(function(tableView) {
        tableView.addCellRenderer(new HTMLRenderer());
    });

});

//# sourceURL=html_render.js