// Loads the TMDL semantic model with Microsoft's Tabular Object Model, the
// same library Power BI tools use, and checks references between objects.
using Microsoft.AnalysisServices.Tabular;

var folder = args.Length > 0 ? args[0] : "NHS Performance.SemanticModel/definition";
var database = TmdlSerializer.DeserializeDatabaseFromFolder(folder);
var model = database.Model;
var problems = new List<string>();

foreach (var rel in model.Relationships.OfType<SingleColumnRelationship>())
{
    if (rel.FromColumn is null || rel.ToColumn is null)
        problems.Add($"relationship {rel.Name} has a missing column");
}
foreach (var table in model.Tables)
{
    if (table.Partitions.Count == 0)
        problems.Add($"table {table.Name} has no partition");
    foreach (var column in table.Columns.OfType<DataColumn>())
    {
        if (string.IsNullOrEmpty(column.SourceColumn) && column.Type != ColumnType.RowNumber)
            problems.Add($"column {table.Name}[{column.Name}] has no source column");
    }
}

Console.WriteLine($"{model.Tables.Count} tables, {model.Tables.Sum(t => t.Columns.Count)} columns, " +
                  $"{model.Tables.Sum(t => t.Measures.Count)} measures, {model.Relationships.Count} relationships, " +
                  $"{model.Expressions.Count} shared expressions, compatibility level {database.CompatibilityLevel}");
foreach (var p in problems) Console.WriteLine($"PROBLEM: {p}");
return problems.Count == 0 ? 0 : 1;
